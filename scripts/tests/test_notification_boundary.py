import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('notification_boundary',Path(__file__).resolve().parents[1]/'notification-boundary.py')
proofs=importlib.util.module_from_spec(spec)
spec.loader.exec_module(proofs)


class NotificationBoundary(unittest.TestCase):
    def report(self):
        scope=json.dumps(['fixture.example',None,None,{},False,3],separators=(',',':')).encode()
        return {'server_name':'fixture.example','dry_run':False,'notification_boundary_mode':'managed_fresh',
                'notification_boundary':{'version':1,'import_id':'a'*64,'scope_sha256':hashlib.sha256(scope).hexdigest(),'high_water':128}}

    def test_completed_fresh_proof_matches_cold_restore(self):
        report=self.report()
        boundary=proofs.require_boundary(report,'fixture.example')
        self.assertEqual(proofs.require_boundary(report,'fixture.example',boundary),boundary)

    def test_legacy_supplementary_dry_and_missing_are_refused(self):
        for mode in (None,'unmanaged_legacy','unmanaged_supplementary','dry_run'):
            report=self.report();report['notification_boundary_mode']=mode
            with self.assertRaises(AssertionError):proofs.require_boundary(report,'fixture.example')
        report=self.report();report['dry_run']=True
        with self.assertRaises(AssertionError):proofs.require_boundary(report,'fixture.example')
        report=self.report();report.pop('notification_boundary')
        with self.assertRaises(AssertionError):proofs.require_boundary(report,'fixture.example')

    def test_incomplete_exhausted_boolean_and_malformed_are_refused(self):
        for field,value in [('high_water',None),('high_water',True),('high_water',-1),('high_water',2**64-1),('high_water',2**64),('version',True),('version',2),('import_id','b'),('scope_sha256','b'*64)]:
            report=self.report();report['notification_boundary'][field]=value
            with self.assertRaises(AssertionError):proofs.require_boundary(report,'fixture.example')

    def test_cross_server_or_replaced_cold_proof_is_refused(self):
        report=self.report();boundary=copy.deepcopy(report['notification_boundary'])
        with self.assertRaises(AssertionError):proofs.require_boundary(report,'other.example')
        for field,value in [('import_id','c'*64),('high_water',129)]:
            changed=copy.deepcopy(report);changed['notification_boundary'][field]=value
            with self.assertRaises(AssertionError):proofs.require_boundary(changed,'fixture.example',boundary)


if __name__=='__main__':unittest.main()
