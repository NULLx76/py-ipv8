from __future__ import absolute_import

import struct

from .cryptosystem.value import FP2Value


__all__ = ['pack_pair', 'unpack_pair', 'BonehPublicKey', 'BonehPrivateKey', 'BitPairAttestation', 'Attestation']


def _num_to_str(num):
    """
    Convert an integer to a str.
    """
    out = b''
    h = hex(num)[2:]
    if h.endswith('L'):
        h = h[:-1]
    if (len(h) % 2) == 1:
        h = '0' + h
    for b in range(0, len(h), 2):
        out += struct.pack(">B", int(h[b] + h[b + 1], 16))
    return out


def _str_to_num(s):
    """
    Convert a str to an integer.
    """
    out = 0
    for i in range(len(s)):
        out <<= 8
        out |= struct.unpack(">B", s[i:i + 1])[0]
    return out


def _pack(num):
    """
    Serialize an integer.
    """
    pnum = _num_to_str(num)
    l = _num_to_str(len(pnum))
    return struct.pack(">B", len(l)) + l + pnum


def _unpack(s):
    """
    Unserialize an integer from a str.
    """
    llen = struct.unpack(">B", s[0:1])[0]
    l = _str_to_num(s[1:1 + llen])
    return _str_to_num(s[1 + llen:llen + l + 1]), s[llen + l + 1:]


def pack_pair(a, b):
    """
    Serialize a pair of two integers.
    """
    return _pack(a) + _pack(b)


def unpack_pair(s):
    """
    Unserialize a pair of two integers.
    """
    a, r = _unpack(s)
    b, r = _unpack(r)
    return a, b, r


class BonehPublicKey(object):
    """
    A public key for Boneh et al.'s cryptosystem.
    """
    FIELDS = 5

    def __init__(self, p, g, h):
        self.p = p
        self.g = g
        self.h = h

    def serialize(self):
        return _pack(self.p) + _pack(self.g.a) + _pack(self.g.b) + _pack(self.h.a) + _pack(self.h.b)

    @classmethod
    def unserialize(cls, s):
        rem = s
        nums = []
        while rem and len(nums) < cls.FIELDS:
            unpacked, rem = _unpack(rem)
            nums.append(unpacked)
        if len(nums) != cls.FIELDS:
            return None
        inits = [nums[0],
                 FP2Value(nums[0], nums[1], nums[2]),
                 FP2Value(nums[0], nums[3], nums[4])]
        if len(nums) > 5:
            inits.append(nums[5])
            inits.append(nums[6])
        return cls(*inits)


class BonehPrivateKey(BonehPublicKey):
    """
    A private key for Boneh et al.'s cryptosystem.
    """
    FIELDS = 7

    def __init__(self, p, g, h, n, t1):
        super(BonehPrivateKey, self).__init__(p, g, h)
        self.n = n
        self.t1 = t1

    def serialize(self):
        return super(BonehPrivateKey, self).serialize() + _pack(self.n) + _pack(self.t1)

    def public_key(self):
        return BonehPublicKey(self.p, self.g, self.h)


class BitPairAttestation(object):
    """
    An attestation of a single bitpair of a larger Attestation.
    """

    def __init__(self, a, b, complement):
        self.a = a
        self.b = b
        self.complement = complement

    def compress(self):
        return self.a * self.b * self.complement

    def serialize(self):
        return (_pack(self.a.a) + _pack(self.a.b) + _pack(self.b.a) + _pack(self.b.b)
                + _pack(self.complement.a) + _pack(self.complement.b))

    @classmethod
    def unserialize(cls, s, p):
        rem = s
        nums = []
        while rem and len(nums) < 6:
            unpacked, rem = _unpack(rem)
            nums.append(unpacked)
        inits = [FP2Value(p, nums[0], nums[1]),
                 FP2Value(p, nums[2], nums[3]),
                 FP2Value(p, nums[4], nums[5])]
        return cls(*inits)


class Attestation(object):
    """
    An attestation for a public key of a value consisting of multiple bitpairs.
    """

    def __init__(self, PK, bitpairs):
        self.bitpairs = bitpairs
        self.PK = PK

    def serialize(self):
        out = b''
        out += self.PK.serialize()
        for bitpair in self.bitpairs:
            out += bitpair.serialize()
        return out

    @classmethod
    def unserialize(cls, s):
        PK = BonehPublicKey.unserialize(s)
        bitpairs = []
        rem = s[len(PK.serialize()):]
        while rem:
            attest = BitPairAttestation.unserialize(rem, PK.p)
            bitpairs.append(attest)
            rem = rem[len(attest.serialize()):]
        return cls(PK, bitpairs)
