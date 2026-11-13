"""工作线程只处理数据；所有界面回调由主线程 poll 触发。"""
import queue
import threading


class BackgroundTasks:
    def __init__(self):
        self.events = queue.Queue()
        self.busy = False
        self.closed = False

    def start(self, work):
        if self.busy or self.closed:
            return False
        self.busy = True
        def run():
            try:
                result = work(lambda message: self.events.put(('progress', message)))
            except Exception as exc:
                self.events.put(('error', exc))
            else:
                self.events.put(('success', result))
        threading.Thread(target=run, daemon=True).start()
        return True

    def poll(self):
        events = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] in ('success', 'error'):
                self.busy = False
            if not self.closed:
                events.append(event)
        return events
