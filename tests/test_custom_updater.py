import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
from Utils import updater
from tg_bot.bot import TGBot


class CustomUpdateTests(unittest.TestCase):
    def test_update_commands_never_download_install_or_backup(self):
        bot=Obj(bot=Obj(send_message=Mock()),cardinal=Obj(VERSION='custom'))
        message=Obj(chat=Obj(id=1))
        with patch.object(updater,'get_new_releases') as fetch,patch.object(updater,'install_release') as install:
            TGBot.update(bot,message)
            TGBot.check_updates(bot,message)
            fetch.assert_not_called()
            install.assert_not_called()
        self.assertEqual(bot.bot.send_message.call_count,2)
    def test_install_release_cannot_copy_or_delete_files(self):
        with patch.object(updater.shutil,'copytree') as tree,patch.object(updater.shutil,'copy2') as copy,patch.object(updater.os,'remove') as remove:
            self.assertEqual(updater.install_release('anything'),1)
            tree.assert_not_called();copy.assert_not_called();remove.assert_not_called()
