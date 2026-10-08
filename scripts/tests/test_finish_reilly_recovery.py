"""Final-witness recovery cannot waive a failed native/cold/archive gate."""
import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'finish-reilly-rehearsal.py'
spec = importlib.util.spec_from_file_location('finish_recovery', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def previous():
    return {'phase':'failed','complete':True,'passed':False,
            'scratch_cleanup_error':'CalledProcessError',
            'candidate_sha256':module.FINAL_SHA,'binary_sha256':module.FINAL_SHA,
            'restored_validation_binary_sha256':module.FINAL_SHA,
            'restored_store':module.RESTORE,'rehearsal_tag':module.TAG,
            'restored_server_ready':True,
            'off_cluster_backup':{'full_object_reread_verified':True,
                                  'overwrite_prevented':True,'bytes':100,
                                  'sha256':'a'*64}}


class FinalWitnessRecoveryTests(unittest.TestCase):
    def test_exact_completed_cold_readback_can_recover_shutdown_only(self):
        module.check_final_witness_recovery(previous())

    def test_missing_cold_or_identity_proof_refuses_recovery(self):
        for key, value in [('restored_validation_binary_sha256','b'*64),
                           ('restored_server_ready',False),
                           ('candidate_sha256','b'*64),
                           ('restored_store','/work/wrong-copy'),
                           ('rehearsal_tag','wrong'),
                           ('scratch_cleanup_error','RuntimeError'),
                           ('passed',True),('complete',False)]:
            with self.subTest(key=key):
                document=previous();document[key]=value
                with self.assertRaises(AssertionError):
                    module.check_final_witness_recovery(document)

    def test_partial_or_unverified_archive_refuses_recovery(self):
        for key,value in [('full_object_reread_verified',False),
                          ('overwrite_prevented',False),('bytes',0),('sha256','partial')]:
            with self.subTest(key=key):
                document=copy.deepcopy(previous());document['off_cluster_backup'][key]=value
                with self.assertRaises(AssertionError):
                    module.check_final_witness_recovery(document)


if __name__=='__main__':unittest.main()
