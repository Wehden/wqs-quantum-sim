"""V2 encrypted creation hook interface, distinct from explicit direct restore."""
from .capture import capture
from .metadata import require
from .v2 import validate_manifest, validate_record


def create(source, manifest, providers, work_dir, requested_id):
    """Called by the configured capture coordinator with WQS_BACKUP_SET_ID.

    The actual coordinator owns source consistency and concrete encryption/storage
    providers. Merely wrapping a stored old receipt cannot create a successful set.
    """
    require(isinstance(requested_id, str) and bool(requested_id), 'requested backup ID required')
    validate_manifest(manifest, requested_id)
    receipt = capture(source, manifest, manifest['context'], providers, work_dir)
    require(receipt['context']['set_id'] == requested_id and receipt['outcome'] == 'readback_verified',
            'capture did not complete')
    result = {'evidence_type': 'creation_result', 'created': True, 'backup_id': requested_id}
    validate_record(result)
    return result


def unavailable_result():
    return {'evidence_type': 'creation_result', 'created': False, 'backup_id': None, 'failure': 'unavailable'}


if __name__ == '__main__':
    # No default live provider/configuration is guessed or activated.
    from .metadata import canonical
    print(canonical(unavailable_result()).decode())
    raise SystemExit(1)
