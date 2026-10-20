"""Exercise public-recipient encryption without hardware, identities or network."""
import subprocess

from bech32 import bech32_encode, convertbits
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

hardware = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
fallback = x25519.X25519PrivateKey.generate().public_key().public_bytes_raw()
recipients = [bech32_encode('age1yubikey', convertbits(hardware, 8, 5, True)),
              bech32_encode('age', convertbits(fallback, 8, 5, True))]
result = subprocess.run(['age', '-r', recipients[0], '-r', recipients[1]],
                        input=b'public recipient encryption check\n', capture_output=True, check=True, timeout=30)
assert result.stdout.startswith(b'age-encryption.org/v1\n')
assert b'-> piv-p256 ' in result.stdout and b'-> X25519 ' in result.stdout
print('YubiKey public recipient and native fallback encryption passed without hardware')
