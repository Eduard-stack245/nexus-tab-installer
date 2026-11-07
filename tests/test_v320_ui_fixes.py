"""Regression coverage for the actual visual/onboarding failures of an early build."""
import ast
from pathlib import Path
from nexus_tab_installer.core.display import clean_mod_title
from nexus_tab_installer.core.links import parse_mod

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')


def test_browser_title_shortening():
    assert clean_mod_title("Example's Prison - French at Skyrim Special Edition Nexus - Mods and Community", 'skyrimspecialedition', 197516) == "Example's Prison - French"
    assert clean_mod_title('skyrimspecialedition / #197516', 'skyrimspecialedition', 197516) == 'Мод #197516'
    assert clean_mod_title('Example - A unique and relentless follower', 'skyrimspecialedition', 197526) == 'Example - A unique and relentless follower'


def test_no_wrapped_row_with_expanding_path():
    # An early combo broke the Flet layout on Windows and created a grey block.
    assert "ft.Row([mo2_ini, ft.Button('Найти MO2', on_click=find_mo2)], wrap=True)" not in APP
    assert "ft.Row([mo2_profile, ft.Button('Профили MO2', on_click=refresh_mo2_profiles)], wrap=True)" not in APP


def test_usable_first_launch_and_single_main_list():
    assert 'Настроить Nexus' in APP
    assert 'Получить API-ключ' in APP
    assert 'Вставить ссылки или открыть JSON' in APP
    assert "('start','Мои моды'" in APP
    assert "('start','Главная'" not in APP


def test_no_nested_scrollable_settings():
    settings = (Path(__file__).resolve().parents[1] / 'src/nexus_tab_installer/ui/views.py').read_text(encoding='utf-8').split('def settings_view(')[1]
    assert settings.count('ft.ScrollMode.AUTO') == 1
    ast.parse(APP)


def test_missing_third_mod_is_not_link_parser_bug():
    link = 'https://www.nexusmods.com/skyrimspecialedition/mods/197526'
    mod = parse_mod(link)
    assert mod and mod['mod_id'] == 197526
