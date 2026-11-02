from pathlib import Path
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from deploy.media_dependency import unpack, hashes, install, restore, NAME


class MediaDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.root=self.base/'server';self.root.mkdir()
        self.job=self.base/'job';self.job.mkdir()

    def wheel(self,names):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            for name in names:
                archive.writestr(name,b'test contents')
        raw=output.getvalue()
        return raw,{'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

    def test_bad_checksum_or_archive_path_never_writes_outside_stage(self):
        for names in [('PIL/../../escape.py',),('outside.py',),('PIL/a.py','PIL/A.py')]:
            raw,settings=self.wheel(names)
            with self.assertRaises(ValueError):
                unpack(raw,settings,self.job/NAME)
            self.assertFalse((self.job/NAME).exists())
        raw,settings=self.wheel(['PIL/__init__.py'])
        settings['sha256']='0'*64
        with self.assertRaises(ValueError):
            unpack(raw,settings,self.job/NAME)
        self.assertFalse((self.job/NAME).exists())

    def prepared(self,existing=False):
        raw,settings=self.wheel(['PIL/__init__.py','pillow-13.0.0.dist-info/licenses/LICENSE'])
        unpack(raw,settings,self.job/NAME)
        state={'existing':existing,'files':hashes(self.job/NAME)}
        (self.job/'media-state.json').write_text(json.dumps(state))

    def test_isolated_install_and_restore_preserve_legacy_dependencies(self):
        legacy=self.root/'vendor-presence-v1'/'PIL'
        legacy.mkdir(parents=True)
        (legacy/'__init__.py').write_text('original library')
        self.prepared()
        install(self.root,self.job)
        self.assertTrue((self.root/NAME/'PIL/__init__.py').exists())
        self.assertTrue((self.root/NAME/'pillow-13.0.0.dist-info/licenses/LICENSE').exists())
        restore(self.root,self.job)
        self.assertFalse((self.root/NAME).exists())
        self.assertEqual((legacy/'__init__.py').read_text(),'original library')

    def test_existing_or_tampered_installation_is_not_overwritten_or_deleted(self):
        self.prepared()
        target=self.root/NAME;target.mkdir()
        (target/'manual.txt').write_text('preserve')
        with self.assertRaises(ValueError):
            install(self.root,self.job)
        with self.assertRaises(ValueError):
            restore(self.root,self.job)
        self.assertEqual((target/'manual.txt').read_text(),'preserve')

    def test_preexisting_verified_isolated_library_survives_rollback(self):
        self.prepared(existing=True)
        import shutil
        shutil.copytree(self.job/NAME,self.root/NAME)
        install(self.root,self.job)
        restore(self.root,self.job)
        self.assertTrue((self.root/NAME/'PIL/__init__.py').exists())


    def test_unsafe_legacy_pillow_never_decodes_untrusted_large_images(self):
        from unittest.mock import patch
        from PIL import Image
        from notification_media import fit_image,MAX_IMAGE_BYTES
        with patch.object(Image,'__version__','12.3.0'), patch.object(Image,'open') as decode:
            self.assertIsNone(fit_image(b'x'*(MAX_IMAGE_BYTES+1),'png','image/png'))
            decode.assert_not_called()
