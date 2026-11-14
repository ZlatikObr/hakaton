"""Small reproducible challenge set, not a population estimate of PII recall."""
import json
from pathlib import Path
from tz_audit.privacy import Redactor

POSITIVES=[
 ('full_name','Документ подписал Иван Петров.', ['Иван Петров']),
 ('first_name','Анна получила копию.', ['Анна']),
 ('inflection','Письмо передано Ивану Петрову.', ['Ивану Петрову']),
 ('patronymic','Иванов Иван Иванович согласовал документ.', ['Иванов Иван Иванович']),
 ('initials','Согласовано: Петров И. И.', ['Петров И. И.']),
 ('latin_name','Prepared by John Smith.', ['John Smith']),
 ('latin_first_name','Approved by Natasha.', ['Natasha']),
 ('email','Контакт: person@example.com', ['person@example.com']),
 ('phone_ru','Телефон +7 (999) 123-45-67', ['+7 (999) 123-45-67']),
 ('phone_de','Phone: +49 30 12345678', ['+49 30 12345678']),
 ('passport','Паспорт: 45 12 345678', ['45 12 345678']),
 ('snils','СНИЛС 123-456-789 00', ['123-456-789 00']),
 ('inn','ИНН: 123456789012', ['123456789012']),
 ('birth','Дата рождения: 01.02.1980', ['01.02.1980']),
 ('address','Адрес: г. Москва, ул. Тестовая, д. 1', ['Москва','Тестовая','д. 1']),
 ('handle','Telegram: @example_person', ['@example_person']),
]
NEGATIVES=[
 'Грузоподъемность 500 кг.', 'ГОСТ Р 53782-2010.', 'Метод Роквелла.', 'Датчик Холла.',
 'Предмет контракта', 'Товара | Поставщиком | Заказчику | Акта',
 'Заказчиком Товар | Товара Поставщиком', 'Совместимость с ОС | Astra Linux',
 'Интерфейсы | Wi-Fi 802.11b/g/n', 'Тип сканирования | Полноцветное',
 'Формат оригинала | Ultra Fine, Manual Transmission', 'Гарантия 12 месяцев.',
 'Поставка в течение 15 рабочих дней.', 'Процессор | Intel Core', 'Стеклянная ванна.',
]

def main():
 result={}
 for backend in ['rules','natasha','spacy','ensemble']:
  r=Redactor(backend=backend);misses=[];false_positives=[]
  for name,text,expected in POSITIVES:
   spans=r.spans(text)
   # Every character of each labelled entity must be covered (not merely a changed substring).
   for entity in expected:
    start=text.index(entity)
    if not all(any(a<=i<b for a,b,_ in spans) for i in range(start,start+len(entity))):misses.append(name);break
  for i,text in enumerate(NEGATIVES):
   if r.spans(text):false_positives.append(i+1)
  result[backend]={'positive_cases':len(POSITIVES),'fully_hidden_cases':len(POSITIVES)-len(misses),'missed_case_ids':misses,'negative_cases':len(NEGATIVES),'false_positive_case_numbers':false_positives}
 path=Path('artifacts/privacy-evaluation.json');path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
