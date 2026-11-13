"""Task delivery state and receipt projections."""
import json


class TaskReports:
    def __init__(self, database):
        self.database = database

    def delivery(self, task_id, state):
        with self.database.transaction() as con:
            con.execute('INSERT INTO task_delivery(task_id,state) VALUES(?,?) '
                        'ON CONFLICT(task_id) DO UPDATE SET state=excluded.state', (task_id, state))

    def receipt(self, row, key):
        identity = json.dumps([row['session'], str(row['sender']), key], ensure_ascii=False)
        rows = self.database.rows('SELECT state FROM notification_receipts WHERE id=?', (identity,))
        return rows[0]['state'] if rows else None
