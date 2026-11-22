"""Plugin discovery only: no AWS sessions, profile reads or system PATH edits."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import deploy_aws as deploy


class TunnelTests(unittest.TestCase):
    def test_existing_path_is_preserved(self):
        original={'PATH':'existing','KEEP':'value'}
        with patch.object(deploy.shutil,'which',return_value='installed'):
            actual=deploy.tunnel_environment(original)
        self.assertEqual(actual,original); self.assertIsNot(actual,original)

    def test_default_windows_install_is_added_only_to_child_environment(self):
        original={'Path':'original','ProgramFiles':'C:/Program Files'}
        with patch.object(deploy.shutil,'which',return_value=None),patch.object(Path,'is_file',return_value=True):
            actual=deploy.tunnel_environment(original,windows=True)
        expected=str(Path('C:/Program Files')/'Amazon'/'SessionManagerPlugin'/'bin')
        self.assertEqual(actual['Path'],expected+os.pathsep+'original')
        self.assertEqual(original['Path'],'original'); self.assertNotIn('PATH',actual)

    def test_alternate_install_and_case_insensitive_environment(self):
        original={'PATH':'original','programfiles':'primary','PROGRAMFILES(X86)':'alternate'}
        with patch.object(deploy.shutil,'which',return_value=None),patch.object(Path,'is_file',side_effect=[False,True]):
            actual=deploy.tunnel_environment(original,windows=True)
        self.assertTrue(actual['PATH'].startswith(str(Path('alternate')/'Amazon'/'SessionManagerPlugin'/'bin')))

    def test_missing_install_fails_before_starting_aws(self):
        with patch.object(deploy.shutil,'which',return_value=None),patch.object(Path,'is_file',return_value=False),patch.object(deploy.subprocess,'call') as call:
            with self.assertRaisesRegex(ValueError,'plugin not found'): deploy.start_tunnel('instance','profile','region',18000)
        call.assert_not_called()

    def test_aws_receives_plugin_environment_and_requested_ports(self):
        with patch.object(deploy,'tunnel_environment',return_value={'PATH':'fixed'}) as environment,patch.object(deploy.subprocess,'call',return_value=0) as call:
            self.assertEqual(deploy.start_tunnel('instance','radiology-dev','us-east-1',18001),0)
        self.assertEqual(call.call_args.kwargs['env'],{'PATH':'fixed'})
        arguments=call.call_args.args[0]
        self.assertEqual(json.loads(arguments[arguments.index('--parameters')+1]),{'portNumber':['8000'],'localPortNumber':['18001']})


if __name__=='__main__': unittest.main()
