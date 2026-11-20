"""Persistent, process-safe CNY reservations; unknown requests retain their reserve."""
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


def micros(value):
    amount=Decimal(str(value))
    if not amount.is_finite() or amount<0:raise ValueError('invalid money amount')
    return int((amount*1_000_000).to_integral_value(rounding=ROUND_CEILING))


class Budget:
    def __init__(self,path,limit):
        self.path=Path(path);self.limit=micros(limit)
        if not self.limit:raise ValueError('budget must be positive')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS budget(limit_micro INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,reserved INTEGER NOT NULL,actual INTEGER,status TEXT NOT NULL);')
            row=db.execute('SELECT limit_micro FROM budget').fetchone()
            if row and row[0]!=self.limit:raise ValueError('existing budget limit differs')
            if not row:db.execute('INSERT INTO budget VALUES(?)',(self.limit,))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            with db:yield db
        finally:db.close()

    @classmethod
    def read_summary(cls,path,limit):
        """Read an existing ledger without creating a file, tables, or reservations."""
        path=Path(path)
        if not path.exists():
            return dict(limitYuan=micros(limit)/1e6,knownYuan=0,committedYuan=0,unknownRequests=0,requests=0,boundExceeded=False)
        db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        try:
            if db.execute('SELECT limit_micro FROM budget').fetchone()[0]!=micros(limit):raise ValueError('existing budget limit differs')
            rows=db.execute('SELECT reserved,actual,status FROM calls').fetchall()
        finally:db.close()
        return cls.summarize(rows,micros(limit))

    def reserve(self,identifier,amount):
        reserved=micros(amount)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            used=db.execute('SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM calls').fetchone()[0]
            if used+reserved>self.limit:raise BudgetExceeded('evaluation budget exhausted')
            db.execute("INSERT INTO calls VALUES(?,?,NULL,'pending')",(identifier,reserved))

    def settle(self,identifier,amount):
        actual=None if amount is None else micros(amount)
        with self.connect() as db:
            row=db.execute('SELECT reserved FROM calls WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ValueError('unknown reservation')
            if actual is not None and actual>row[0]:
                # Keep the larger actual amount; block subsequent calls rather than hiding the overrun.
                db.execute("UPDATE calls SET actual=?,status='bound_exceeded' WHERE id=?",(actual,identifier))
            else:db.execute('UPDATE calls SET actual=?,status=? WHERE id=?',(actual,'known' if actual is not None else 'unknown',identifier))

    def summary(self):
        with self.connect() as db:
            rows=db.execute('SELECT reserved,actual,status FROM calls').fetchall()
        return self.summarize(rows,self.limit)

    @staticmethod
    def summarize(rows,limit):
        return dict(limitYuan=limit/1e6,knownYuan=sum(a or 0 for _,a,_ in rows)/1e6,
                    committedYuan=sum(a if a is not None else r for r,a,_ in rows)/1e6,
                    unknownRequests=sum(a is None for _,a,_ in rows),requests=len(rows),
                    boundExceeded=any(s=='bound_exceeded' for _,_,s in rows))
