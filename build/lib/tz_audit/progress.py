"""In-memory, content-free progress and cooperative cancellation with socket abort."""
import threading
from datetime import datetime, timezone


class RunCancelled(Exception):
    pass


class RunControl:
    def __init__(self):
        self.lock = threading.RLock()
        self.event = threading.Event()
        self.aborters = set()
        self.state = {'status': 'queued', 'stage': 'В очереди', 'events': []}
        self.emit('В очереди')

    def check(self):
        if self.event.is_set():
            raise RunCancelled()

    def emit(self, stage, **details):
        with self.lock:
            event = {'seq': len(self.state['events'])+1, 'time': datetime.now(timezone.utc).isoformat(), 'message': stage}
            self.state['stage'] = stage
            self.state['events'].append(event)
            self.state.update(details)

    def snapshot(self):
        with self.lock:
            return {**self.state, 'events': [e.copy() for e in self.state['events']]}

    def start(self):
        with self.lock:
            self.check()
            self.state['status'] = 'running'
            self.emit('Загружаем обезличенный документ')

    def register_abort(self, callback):
        with self.lock:
            self.check()
            self.aborters.add(callback)

    def unregister_abort(self, callback):
        with self.lock:
            self.aborters.discard(callback)

    def cancel(self):
        with self.lock:
            if self.state['status'] in {'complete', 'failed', 'cancelled'}:
                return False
            if self.event.is_set():
                return True
            self.event.set()
            queued = self.state['status'] == 'queued'
            self.state['status'] = 'cancelled' if queued else 'cancelling'
            self.emit('Задание отменено до запуска' if queued else 'Отмена запрошена; останавливаем текущую операцию')
            callbacks = list(self.aborters)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass
        return True

    def cancelled(self):
        with self.lock:
            if self.state['status'] != 'cancelled':
                self.state['status'] = 'cancelled'
                self.emit('Работа отменена. Итоговый документ не создан')

    def fail(self, message):
        with self.lock:
            if self.event.is_set():
                self.cancelled()
                return
            self.state.update(status='failed', error=message)
            self.emit('Обработка остановлена с ошибкой')

    def commit(self, publish, **details):
        # Cancellation and publication are linearized: no partial result after successful cancellation.
        with self.lock:
            self.check()
            publish()
            self.state.update(status='complete', **details)
            self.emit('Готово. Итоговый Word и отчёт сохранены')
