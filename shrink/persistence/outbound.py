import time


def record_delivery(app, source, text, delivery_id):
    db = app.database
    delivery_id = source["source"] + ":" + delivery_id
    with db.transaction() as con:
        con.execute("CREATE TABLE IF NOT EXISTS outbound_receipts(id TEXT PRIMARY KEY,created REAL)")
        if con.execute("SELECT 1 FROM outbound_receipts WHERE id=?", (delivery_id,)).fetchone():
            return True
        now = time.time()
        con.execute("""INSERT OR IGNORE INTO messages
            (id,user_id,source,source_name,speaker_name,text,quote,mentions,has_image,reply_to,
             addressee,is_bot,learnable,learned,digested,created)
            VALUES(?,?,?,?,?,?,?,'[]',0,'',?,1,0,1,0,?)""",
            ("notification:" + delivery_id, "self", source["source"], "", "石英", text, "{}",
             source["user_id"], now))
        con.execute("INSERT INTO outbound_receipts VALUES(?,?)", (delivery_id, time.time()))
    return True
