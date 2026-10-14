#!/usr/bin/env python3
"""Seals a file the way the Dota Forge app's PackCodec opens it:
gzip, then AES-256-GCM, stored as nonce (12 bytes) + ciphertext + tag (16).

    PACK_KEY=<64 hex chars> python3 scripts/seal_pack.py in.json out.bin

The key is the repository secret PACK_KEY. It is not real secrecy (the app
carries the same key); it keeps the data from being read straight off the
repository or out of the app.
"""

import gzip
import os
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def main():
    source, target = sys.argv[1], sys.argv[2]
    key = bytes.fromhex(os.environ['PACK_KEY'])
    if len(key) != 32:
        sys.exit('PACK_KEY must be 32 bytes (64 hex characters)')
    with open(source, 'rb') as f:
        compressed = gzip.compress(f.read())
    nonce = os.urandom(12)
    sealed = nonce + AESGCM(key).encrypt(nonce, compressed, None)
    with open(target, 'wb') as f:
        f.write(sealed)
    print(f'sealed {target} ({len(sealed) // 1024} KB)')


if __name__ == '__main__':
    main()
