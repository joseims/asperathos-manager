from importlib import import_module
import subprocess


def install_plugin(plugin_repository):
    repo_link = 'git+' + plugin_repository
    try:
        exit_status = subprocess.check_call(['pip',
                                             'install',
                                             repo_link])
    except Exception:
        return False
    return exit_status == 0

def get_plugin(plugin_module):
    try:
        plugin = import_module(plugin_module).plugin
    except ImportError:
        raise Exception("Plugin {} is not installed".format(plugin_module))
    return plugin()
