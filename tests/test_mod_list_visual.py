from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / 'src/nexus_tab_installer/ui/flet_app.py').read_text(encoding='utf-8')
VIEWS = (ROOT / 'src/nexus_tab_installer/ui/views.py').read_text(encoding='utf-8')


def test_mod_list_uses_consistent_card_radii_and_background():
    assert "bgcolor='#1B2233' if choice.selected else '#171B29'" in APP
    assert 'border_radius=12' in APP
    assert "bgcolor='#151A28'" in VIEWS


def test_mod_list_selection_contrast():
    assert "ft.Border.all(1, '#514577' if choice.selected else '#272D42')" in APP
    assert "focused_border_color='#9D86FF'" in APP


def test_no_nested_scrolling_on_mods_view():
    src=VIEWS.split('def mods_view(', 1)[1].split('def downloads_view(', 1)[0]
    assert src.count('scroll=ft.ScrollMode.AUTO') == 1
    assert 'height=370' not in src and 'height=460' not in src
    assert 'list_area,' in src


def test_single_main_screen_renders_mods_and_import():
    start = APP.split('def make_start():', 1)[1].split('tab_controls =', 1)[0]
    assert 'workflow_rows' in start
    assert 'import_options' in start
    assert 'link_field' in APP.split('import_options =', 1)[1]
    assert 'import_options' in start
    assert 'api_notice' in start
    assert "('mods','Мои моды'" not in APP


def test_version_visible_to_distinguish_old_launch():
    assert "v1.0.0 · Portable" in APP
