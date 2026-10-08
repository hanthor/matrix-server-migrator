"""Imported scratch stores must not inherit enabled notification defaults."""
import importlib.util
from pathlib import Path
import tomllib
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'finish-reilly-rehearsal.py'
spec=importlib.util.spec_from_file_location('finish_inert',SCRIPT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class InertConfigTests(unittest.TestCase):
    def test_omitted_delivery_sections_are_explicitly_disabled(self):
        config='[server]\nname="reilly.asia"\n[storage]\npath="/work/cold"\n'
        result=tomllib.loads(module.inert_scratch_config(config))
        for section in ('federation','push','previews'):
            self.assertFalse(result[section]['enabled'])
        self.assertEqual(result['storage']['path'],'/work/cold')

    def test_enabled_sections_and_comments_preserve_other_settings(self):
        config='[server]\nname="reilly.asia"\n[federation] # networking\nenabled = true # production default\npeers=["example.org"]\n[push]\nenabled=true\nallow_internal=[]\n[previews]\nallow_private=[]\n[auth]\nbuiltin_oidc=true\n'
        result=tomllib.loads(module.inert_scratch_config(config))
        self.assertEqual(result['federation'],{'enabled':False,'peers':['example.org']})
        self.assertEqual(result['push'],{'enabled':False,'allow_internal':[]})
        self.assertEqual(result['previews'],{'enabled':False,'allow_private':[]})
        self.assertTrue(result['auth']['builtin_oidc'])

    def test_appservice_delivery_is_refused(self):
        with self.assertRaises(AssertionError):
            module.inert_scratch_config('[server]\nname="reilly.asia"\n[appservices]\nregistrations=["/private/registration.yaml"]\n')


if __name__=='__main__':unittest.main()
