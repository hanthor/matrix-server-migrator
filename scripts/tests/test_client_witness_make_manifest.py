import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "make_manifest",
    Path(__file__).resolve().parents[1] / "client-witness" / "make_manifest.py",
)
make_manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_manifest)

USER = "@alice:fixture.example"
KEY_KINDS = ["master_keys", "self_signing_keys", "user_signing_keys"]


def reference():
    return {
        "user_id": USER,
        "provenance": "trusted client export",
        "source_keys_query": {
            kind: {USER: {"keys": {"ed25519:" + kind: "key-" + kind}}} for kind in KEY_KINDS
        },
        "decrypted_events": [
            {
                "original_type": "m.room.encrypted",
                "type": "m.room.message",
                "room_id": "!room:fixture.example",
                "event_id": "$one",
                "content": {"msgtype": "m.text", "body": "hello secret"},
            }
        ],
    }


class MakeManifest(unittest.TestCase):
    def run_main(self, ref, output_name="manifest.json", output_exists=False):
        """Run main() against a reference; return (exit code, stdout, output path)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ref_path = root / "reference.json"
        ref_path.write_text(json.dumps(ref))
        out_path = root / output_name
        if output_exists:
            out_path.write_text("keep")
        argv = ["make_manifest.py", "--reference", str(ref_path), "--output", str(out_path)]
        stdout = io.StringIO()
        code = None
        # main() sets a process-wide umask; restore it so other tests are unaffected.
        old_umask = os.umask(0o022)
        try:
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                try:
                    make_manifest.main()
                except SystemExit as exit_:
                    code = exit_.code
        finally:
            os.umask(old_umask)
        return code, stdout.getvalue(), out_path

    def test_valid_reference_is_hashed_without_plaintext(self):
        code, stdout, out = self.run_main(reference())
        self.assertIsNone(code)
        self.assertEqual(stdout.strip(), "Trusted reference hashed; no server contacted")
        text = out.read_text()
        self.assertNotIn("hello secret", text)
        manifest = json.loads(text)
        self.assertEqual(manifest["user_id"], USER)
        self.assertEqual(manifest["provenance"], "trusted client export")
        self.assertEqual(
            manifest["samples"],
            [
                {
                    "room_id": "!room:fixture.example",
                    "event_id": "$one",
                    "body_sha256": hashlib.sha256(b"hello secret").hexdigest(),
                }
            ],
        )

    def test_cross_signing_digest_is_canonical_json_of_the_three_identities(self):
        _, _, out = self.run_main(reference())
        ref = reference()
        identities = {k: ref["source_keys_query"][k][USER]["keys"] for k in KEY_KINDS}
        canonical = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(
            json.loads(out.read_text())["cross_signing_keys_sha256"],
            hashlib.sha256(canonical).hexdigest(),
        )

    def test_output_is_private_to_the_owner(self):
        _, _, out = self.run_main(reference())
        self.assertEqual(out.stat().st_mode & 0o077, 0)

    def assert_rejected(self, ref, expected_error):
        code, stdout, out = self.run_main(ref)
        self.assertEqual(code, 1)
        self.assertEqual(stdout.strip(), "Reference rejected: " + expected_error)
        self.assertFalse(out.exists())
        self.assertNotIn("hello secret", stdout)

    def test_rich_content_is_rejected(self):
        for extra in (
            {"formatted_body": "<b>hello secret</b>"},
            {"m.relates_to": {"rel_type": "m.replace"}},
            {"m.new_content": {"body": "x"}},
        ):
            ref = reference()
            ref["decrypted_events"][0]["content"].update(extra)
            with self.subTest(extra=sorted(extra)):
                self.assert_rejected(ref, "AssertionError")

    def test_non_text_message_types_are_rejected(self):
        ref = reference()
        ref["decrypted_events"][0]["content"]["msgtype"] = "m.image"
        self.assert_rejected(ref, "AssertionError")

    def test_event_that_was_not_encrypted_or_not_a_message_is_rejected(self):
        for field, value in (("original_type", "m.room.message"), ("type", "m.reaction")):
            ref = reference()
            ref["decrypted_events"][0][field] = value
            with self.subTest(field=field):
                self.assert_rejected(ref, "AssertionError")

    def test_empty_samples_or_missing_provenance_is_rejected(self):
        ref = reference()
        ref["decrypted_events"] = []
        self.assert_rejected(ref, "AssertionError")
        ref = reference()
        ref["provenance"] = ""
        self.assert_rejected(ref, "AssertionError")

    def test_missing_identity_key_reports_only_the_error_class(self):
        ref = reference()
        del ref["source_keys_query"]["master_keys"]
        self.assert_rejected(ref, "KeyError")

    def test_existing_output_is_never_overwritten(self):
        code, stdout, out = self.run_main(reference(), output_exists=True)
        self.assertEqual(code, 1)
        self.assertEqual(stdout.strip(), "Reference rejected: FileExistsError")
        self.assertEqual(out.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
