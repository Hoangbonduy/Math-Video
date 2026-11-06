"""Runtime API namespace coverage, including exact production failure spellings."""
from unittest.mock import patch
import pytest
from src.scene_design import scene_references as refs
from src.shared._workflow_store import WorkflowError


def test_namespaces_and_methods_resolve_in_installed_runtime():
    names=['Text','manim.Text','Axes.plot','manim.Axes.plot','vn_text','manim_kit.vn_text',
        'src.scene_design.manim_kit.vn_text','src.scene_design.manim_kit.track','MIN_FONT_SIZE','manim_kit.MIN_FONT_SIZE']
    result=refs.lookup(names,['group_motion'])
    assert not result['missing']
    for a,b in [('Text','manim.Text'),('Axes.plot','manim.Axes.plot'),('vn_text','manim_kit.vn_text'),
                ('vn_text','src.scene_design.manim_kit.vn_text')]:
        assert result['installed_apis'][a]['signature']==result['installed_apis'][b]['signature']
    assert result['installed_apis']['MIN_FONT_SIZE']['module'].endswith('manim_kit')
    assert result['installed_apis']['manim.Text']['module']=='manim'
    assert result['installed_apis']['src.scene_design.manim_kit.track']['module'].endswith('manim_kit')


@pytest.mark.parametrize('name',['','manim..Text','manim._private','os.system','src.other.module.fn',
    'src.scene_design.manim_kit.__dict__','manim.Text()','manim.Text;bad',None,42])
def test_bad_names_fail_with_specific_feedback_before_starting_a_process(name):
    with patch.object(refs.subprocess,'run',side_effect=AssertionError('Must reject before subprocess')):
        with pytest.raises(WorkflowError) as error:refs.lookup([name],['group_motion'])
    assert error.value.code=='INVALID_SCENE_REFERENCE'
    assert 'at most 16' not in str(error.value)


def test_api_count_and_recipe_errors_are_distinct():
    with pytest.raises(WorkflowError,match='received 17'):
        refs.lookup(['Text']*17,['group_motion'])
    with pytest.raises(WorkflowError,match='Unknown Manim recipe'):
        refs.lookup(['Text'],['not_a_recipe'])


def test_nonexistent_public_api_is_missing_instead_of_a_format_error():
    result=refs.lookup(['manim.UnavailableFixtureAPI','src.scene_design.manim_kit.NotAHelper'],['group_motion'])
    assert result['missing']==['manim.UnavailableFixtureAPI','src.scene_design.manim_kit.NotAHelper']
