"""Local gold checklist. It never runs a model or sends data externally.
Coverage is lexical triage for a human evaluator, NOT semantic precision/recall.
"""
import argparse
import hashlib
import json
from pathlib import Path
from tz_audit.models import Report

GOLD = {
    'lift': [
        ('Тип лифта', ['contradiction', 'characteristics'], ['пассажирский', 'грузовой']),
        ('Грузоподъемность', ['contradiction', 'characteristics'], ['300', '500']),
        ('Высота дверного проема', ['contradiction', 'characteristics'], ['2100', '2000']),
        ('Гарантия на работы', ['warranty'], ['товарной накладной']),
        ('Код работ', ['classification'], ['43.29.19.120']),
        ('Состав спецификации', ['completeness', 'classification'], ['замене']),
        ('Обозначение и статус стандарта', ['standards'], ['53782']),
    ],
    'metrology': [
        ('Состав комплектов и отдельные товары', ['completeness', 'classification'], ['комплект']),
        ('Коды оборудования', ['classification'], ['26.51']),
        ('Характеристики и комплектность', ['characteristics', 'completeness'], []),
        ('Пусконаладка и инструктаж', ['acceptance', 'completeness'], ['инструктаж']),
        ('Национальный режим', ['competition', 'other'], ['национального режима']),
    ],
}

def evaluate(report, case):
    rows = []
    for title, categories, words in GOLD[case]:
        candidates = []
        for i, finding in enumerate(report.review.findings, 1):
            anchors = ' '.join(a.quote for a in finding.anchors).casefold()
            if finding.category in categories and all(word.casefold() in anchors for word in words):
                candidates.append(i)
        rows.append({'criterion': title, 'candidate_findings': candidates, 'human_verdict': None})
    return {'mode': report.mode, 'model': report.model, 'checklist': rows,
        'stats': report.stats, 'note': 'Кандидаты подобраны по категории и цитатам. Полноту, ложные срабатывания и качество исправлений оценивает человек. Эталонные исправления не удостоверяют актуальность норм.'}

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('report', type=Path)
    p.add_argument('--case', choices=GOLD, required=True)
    args = p.parse_args()
    print(json.dumps(evaluate(Report.model_validate_json(args.report.read_bytes()), args.case), ensure_ascii=False, indent=2))
