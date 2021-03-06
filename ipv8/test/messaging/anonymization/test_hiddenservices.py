from __future__ import absolute_import

import six

from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks

from .test_community import MockDHTProvider
from ...base import TestBase
from ...mocking.exit_socket import MockTunnelExitSocket
from ...mocking.ipv8 import MockIPv8
from ....messaging.anonymization.community import CIRCUIT_TYPE_RP_DOWNLOADER, TunnelSettings
from ....messaging.anonymization.hidden_services import HiddenTunnelCommunity
from ....messaging.anonymization.tunnel import IntroductionPoint, PEER_FLAG_EXIT_ANY
from ....peer import Peer


class TestHiddenServices(TestBase):

    def setUp(self):
        super(TestHiddenServices, self).setUp()
        self.initialize(HiddenTunnelCommunity, 3)

        self.private_nodes = []
        self.service = b'0' * 20
        self.received_packets = []

    def tearDown(self):
        for node in self.private_nodes:
            node.unload()
        return super(TestHiddenServices, self).tearDown()

    def get_e2e_circuit_path(self):
        """
        Return the e2e circuit information which is extracted from the nodes.
        Useful for debugging purposes or to verify whether the e2e circuit is correctly established.
        """
        path = []

        # Find the first node of the e2e circuit
        e2e_circuit = None
        first_node = None
        for node in self.nodes:
            for circuit in list(node.overlay.circuits.values()):
                if circuit.ctype == CIRCUIT_TYPE_RP_DOWNLOADER:
                    first_node = node
                    e2e_circuit = circuit
                    break

        if not e2e_circuit:
            # We didn't find any e2e circuit.
            return None

        def get_node_with_sock_addr(sock_addr):
            # Utility method to quickly return a node with a specific socket address.
            for node in self.nodes:
                if node.overlay.my_peer.address == sock_addr:
                    return node
            return None

        # Add the first node to the path
        path.append((first_node.overlay.my_peer.address, e2e_circuit))

        cur_tunnel = e2e_circuit
        while True:
            next_node = get_node_with_sock_addr(cur_tunnel.peer.address)
            if cur_tunnel.circuit_id not in next_node.overlay.relay_from_to:
                # We reached the end of our e2e circuit.
                path.append((next_node.overlay.my_peer.address, cur_tunnel))
                break
            cur_tunnel = next_node.overlay.relay_from_to[cur_tunnel.circuit_id]
            path.append((next_node.overlay.my_peer.address, cur_tunnel))

        return path

    def create_node(self):
        # Initialize a HiddenTunnelCommunity without circuits or exit node functionality
        settings = TunnelSettings()
        settings.min_circuits = 0
        settings.max_circuits = 0
        ipv8 = MockIPv8(u"curve25519", HiddenTunnelCommunity, settings=settings)

        ipv8.overlays = []
        ipv8.strategies = []
        ipv8.overlay.ipv8 = ipv8

        # Then kill all automated circuit creation
        ipv8.overlay.cancel_all_pending_tasks()
        # Finally, use the proper exitnode and circuit settings for manual creation
        ipv8.overlay.settings.min_circuits = 1
        ipv8.overlay.settings.max_circuits = 1
        ipv8.overlay.dht_provider = MockDHTProvider(ipv8.overlay.my_peer)
        return ipv8

    @inlineCallbacks
    def create_intro(self, node_nr, service, required_ip=None):
        """
        Create an 1 hop introduction point for some node for some service.
        """
        self.nodes[node_nr].overlay.create_introduction_point(service, required_ip=required_ip)

        yield self.deliver_messages()

        for node in self.nodes:
            exit_sockets = node.overlay.exit_sockets
            for exit_socket in exit_sockets:
                exit_sockets[exit_socket] = MockTunnelExitSocket(exit_sockets[exit_socket])

    @inlineCallbacks
    def assign_exit_node(self, node_nr):
        """
        Give a node a dedicated exit node to play with.
        """
        exit_node = self.create_node()
        self.private_nodes.append(exit_node)
        exit_node.overlay.settings.peer_flags |= PEER_FLAG_EXIT_ANY
        public_peer = Peer(exit_node.my_peer.public_key, exit_node.my_peer.address)
        self.nodes[node_nr].network.add_verified_peer(public_peer)
        self.nodes[node_nr].network.discover_services(public_peer, exit_node.overlay.master_peer.mid)
        self.nodes[node_nr].overlay.candidates[public_peer] = exit_node.overlay.settings.peer_flags
        self.nodes[node_nr].overlay.build_tunnels(1)
        yield self.deliver_messages()
        exit_sockets = exit_node.overlay.exit_sockets
        for exit_socket in exit_sockets:
            exit_sockets[exit_socket] = MockTunnelExitSocket(exit_sockets[exit_socket])

    @inlineCallbacks
    def test_create_introduction_point(self):
        """
        Check if setting up an introduction point works.
        Some node, other than the instigator, should be assigned as the intro point.
        """
        self.nodes[0].overlay.join_swarm(self.service, 1)
        yield self.introduce_nodes()
        yield self.create_intro(0, self.service)
        seeder_sk = self.nodes[0].overlay.swarms[self.service].seeder_sk
        seeder_pk = seeder_sk.pub().key_to_bin()

        intro_made = False
        for node_nr in range(1, len(self.nodes)):
            intro_made |= seeder_pk in self.nodes[node_nr].overlay.intro_point_for

        self.assertTrue(intro_made)

    @inlineCallbacks
    def test_dht_lookup_with_counterparty(self):
        """
        Check if a DHT lookup works.

        Steps:
         1. Create an introduction point
         2. Do a DHT lookup
         3. Create a rendezvous point
         4. Link the circuit e2e
         5. Callback the service handler
         6. Send data
        """
        callback_called = Deferred()

        self.nodes[0].overlay.join_swarm(self.service, 1, callback_called.callback, seeding=False)
        self.nodes[2].overlay.join_swarm(self.service, 1, callback_called.callback)

        yield self.introduce_nodes()
        yield self.create_intro(2, self.service)
        yield self.assign_exit_node(0)

        self.nodes[0].overlay.do_peer_discovery()
        yield self.deliver_messages()

        yield callback_called

        # Verify the length of the e2e circuit
        e2e_path = self.get_e2e_circuit_path()
        self.assertEqual(len(e2e_path), 4)

        # Check if data can be sent over the e2e circuit
        data = b'PACKET'
        _, circuit = e2e_path[0]
        self.nodes[2].overlay.on_raw_data = lambda _, __, rdata: self.received_packets.append(rdata)
        reactor.callInThread(self.nodes[0].overlay.send_data, [circuit.peer], circuit.circuit_id, ('0.0.0.0', 0),
                             ('0.0.0.0', 0), data)
        yield self.deliver_messages()
        self.assertEqual(len(self.received_packets), 1)
        self.assertEqual(self.received_packets[0], data)

    @inlineCallbacks
    def test_dht_lookup_no_counterparty(self):
        """
        Check if a DHT lookup doesn't return on its own required service.
        Ergo, no self-introduction.
        """
        def callback(_):
            callback.called = True

        callback.called = False

        self.nodes[0].overlay.join_swarm(self.service, 1, callback)

        yield self.introduce_nodes()
        yield self.assign_exit_node(0)

        self.nodes[0].overlay.do_peer_discovery()
        yield self.deliver_messages()

        self.assertFalse(callback.called)

    @inlineCallbacks
    def test_pex_lookup(self):
        # Nodes 1 and 2 are introduction points for node 0
        self.nodes[0].overlay.join_swarm(self.service, 1)
        yield self.introduce_nodes()
        yield self.create_intro(0, self.service, required_ip=self.nodes[1].my_peer)
        yield self.create_intro(0, self.service, required_ip=self.nodes[2].my_peer)

        # Introduce nodes in the PexCommunity
        self.nodes[1].overlay.ipv8.overlays[0].walk_to(self.nodes[2].endpoint.wan_address)
        self.nodes[2].overlay.ipv8.overlays[0].walk_to(self.nodes[1].endpoint.wan_address)
        yield self.deliver_messages()

        # Add node 3 to the experiment and give this node a 1 hop data circuit (to be used for get-peers messages)
        self.add_node_to_experiment(self.create_node())
        yield self.assign_exit_node(3)

        # Ensure node 3 already knows node 2 (which enables PEX) and do a peers-request
        intro_point = IntroductionPoint(self.nodes[2].overlay.my_peer,
                                        self.nodes[0].overlay.swarms[self.service].seeder_sk.pub().key_to_bin())
        self.nodes[3].overlay.join_swarm(self.service, 1, seeding=False)
        self.nodes[3].overlay.swarms[self.service].add_intro_point(intro_point)
        self.nodes[3].overlay.do_peer_discovery()
        yield self.deliver_messages()

        # Node 2 should be known as an introduction point
        peers = [ip.peer for ip in self.nodes[3].overlay.swarms[self.service].intro_points]
        six.assertCountEqual(self, peers, [self.nodes[1].my_peer, self.nodes[2].my_peer])

    @inlineCallbacks
    def test_pex_lookup_exit_is_ip(self):
        # Nodes 1 and 2 are introduction points for node 0
        self.nodes[0].overlay.join_swarm(self.service, 1)
        yield self.introduce_nodes()
        yield self.create_intro(0, self.service, required_ip=self.nodes[1].my_peer)
        yield self.create_intro(0, self.service, required_ip=self.nodes[2].my_peer)

        # Introduce nodes in the PexCommunity
        self.nodes[1].overlay.ipv8.overlays[0].walk_to(self.nodes[2].endpoint.wan_address)
        self.nodes[2].overlay.ipv8.overlays[0].walk_to(self.nodes[1].endpoint.wan_address)
        yield self.deliver_messages()

        # Add node 3 to the experiment and give this node a 1 hop data circuit (to be used for get-peers messages).
        # The data circuit ends in node 1, which is also an introduction point.
        self.add_node_to_experiment(self.create_node())
        self.nodes[3].overlay.create_circuit(1, required_exit=self.nodes[1].overlay.my_peer)
        yield self.deliver_messages()

        # Ensure node 3 already knows node 1 and do a peers-request. Since node 3 already has a data circuit
        # to node 1, it should send a cell directly to node 3.
        intro_point = IntroductionPoint(self.nodes[1].overlay.my_peer,
                                        self.nodes[0].overlay.swarms[self.service].seeder_sk.pub().key_to_bin())
        self.nodes[3].overlay.join_swarm(self.service, 1, seeding=False)
        self.nodes[3].overlay.swarms[self.service].add_intro_point(intro_point)
        self.nodes[3].overlay.do_peer_discovery()
        yield self.deliver_messages()

        # Node 2 should be known as an introduction point
        peers = [ip.peer for ip in self.nodes[3].overlay.swarms[self.service].intro_points]
        six.assertCountEqual(self, peers, [self.nodes[1].my_peer, self.nodes[2].my_peer])
