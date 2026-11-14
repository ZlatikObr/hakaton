import pytest
from tz_audit.privacy import Redactor, common_word, product_term
from tz_audit.models import Finding


@pytest.mark.parametrize('text', [
    'Цвет чернил: синий', 'Материал: полипропилен', 'Тиснение логотипа на обложке',
    'Ручка Cerruti 1881 или эквивалент', 'Бренд: Portobello',
])
def test_product_attributes_are_not_hidden(text):
    assert Redactor().redact(text) == text


@pytest.mark.parametrize('text,secret', [
    ('Контактное лицо: Иванов Петр Сергеевич', 'Иванов'),
    ('Ответственный Смирнова согласует макет.', 'Смирнова'),
    ('Ответственный: John Smith, менеджер', 'John Smith'),
    ('тел. +7 916 123-45-67', '916'),
])
def test_real_personal_data_is_still_hidden(text, secret):
    assert secret not in Redactor().redact(text)


def test_helpers():
    assert common_word('Полипропилен') and common_word('Тиснение') and not common_word('Иванов')
    assert product_term('Dagger', 'Модель: ', ' Soft Touch') and not product_term('John Smith', 'Ответственный: ', ', менеджер')


def test_language_category_exists():
    f = Finding(category='language', severity='low', status='document_issue', title='Опечатки и оформление',
                anchors=[{'block_id': 'b0001', 'quote': 'длинна'}], explanation='Орфографическая ошибка в слове.',
                recommendation='Исправить написание слова.', edits=[{'block_id': 'b0001', 'before': 'длинна', 'after': 'длина'}] * 12)
    assert f.category == 'language' and len(f.edits) == 12
