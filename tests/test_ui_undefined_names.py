"""Guard against NameError in callbacks that unit tests don't execute.

Checks referenced globals at every lexical scope in the Flet controller, including
callbacks nested inside main(). Unlike a plain compile check this catches missing
imports such as discover_mo2 and deploy before distribution.
"""
import ast
import builtins
import symtable
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / 'src/nexus_tab_installer/ui/flet_app.py'


def test_every_ui_callback_resolves_global_names():
    source = APP.read_text(encoding='utf-8')
    tree = ast.parse(source, filename=str(APP))
    table = symtable.symtable(source, str(APP), 'exec')
    module_symbols = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_symbols.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_symbols.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                module_symbols.update(child.id for child in ast.walk(target) if isinstance(child, ast.Name))

    unresolved = set()

    def examine(scope):
        for symbol in scope.get_symbols():
            name = symbol.get_name()
            if symbol.is_global() and symbol.is_referenced() and name not in module_symbols and name not in vars(builtins):
                unresolved.add((scope.get_name(), name))
        for child in scope.get_children():
            examine(child)

    examine(table)
    assert not unresolved, f'Unresolved globals in Flet UI: {sorted(unresolved)}'


def test_manager_apis_imported_from_actual_modules():
    from nexus_tab_installer.integrations.discovery import discover_mo2, discover_vortex
    from nexus_tab_installer.integrations.managed_install import InstallError, deploy, uninstall

    assert all(callable(fn) for fn in (discover_mo2, discover_vortex, deploy, uninstall))
    assert issubclass(InstallError, Exception)
