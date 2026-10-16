"""Export the tested local image and record content identity."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from .release import archive_config


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def export(target, image, sha):
    root = Path('build/release')
    root.mkdir(parents=True, exist_ok=True)
    archive = root / (target + '.tar')
    subprocess.run(['docker', 'save', '-o', str(archive), image], check=True)
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
    assert info['Config']['Labels']['org.opencontainers.image.revision'] == sha
    config_digest, _ = archive_config(archive, image)
    (root / (target + '.json')).write_text(json.dumps({
        'schema': 1, 'source_sha': sha, 'target': target, 'archive': archive.name,
        'archive_sha256': checksum(archive), 'image_id': info['Id'],
        'config_digest': config_digest, 'image_ref': image,
        'platform': info['Os'] + '/' + info['Architecture']}, indent=2) + '\n')


if __name__ == '__main__':
    export(*sys.argv[1:])
