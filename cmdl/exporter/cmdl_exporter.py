import tempfile
from pathlib import Path
from ...kms.kms import KMS
from ...evm.evm import EVM
from .cmdl_cooker import cook
from ...util.atomic_output import atomic_output


def main(cmdl_file, collection_name, evmMode=False, bigMode=False, native_file=None):
    if native_file is None:
        from ...kms.exporter import kms_exporter
        from ...evm.exporter import evm_exporter

        with tempfile.TemporaryDirectory(prefix='sealouse-cook-') as directory:
            path = Path(directory) / ('model.evm' if evmMode else 'model.kms')
            (evm_exporter if evmMode else kms_exporter).main(str(path), collection_name)
            return main(cmdl_file, collection_name, evmMode, bigMode, str(path))
    with open(native_file, 'rb') as stream:
        model = (EVM() if evmMode else KMS()).fromFile(stream)
    # build the CMDL from the exported KMS/EVM data.
    result = cook(model, evmMode)
    with atomic_output(cmdl_file) as stream:
        result.writeToFile(stream)
    return {'FINISHED'}
