from __future__ import annotations
from ast import Index
from operator import not_
import sqlite3
import atexit
from enum import Enum
from typing import Iterator, Literal

from ..utils import jdumps, jloads, toLetters

STAR: Literal['*'] = '*'
class DType(Enum):
    TEXT = 'TEXT'
    INT = 'INTEGER'
    REAL = 'REAL'

class Field:
    def __init__(self, name: str, is_pk: bool=False, dtype: DType=DType.TEXT, not_null:bool=True) -> None:
        self.name = name
        self.is_pk = is_pk
        self.dtype = dtype
        self.not_null = not_null

        self.sql = f'{self.name} {dtype.value} NOT NULL'

class Table:
    def __init__(self, db, name, fields, type='') -> None:
        """use Database.MakeTable() instead of initializer"""
        assert any([f.is_pk for f in fields]), "primary key not specified"
        self.name: str = name
        self.fields: dict[str, Field] = dict((f.name, f) for f in fields)
        self._db = db
        self._cur = db._cur
        self.type: str = type
        self.is_new: bool = False

        foreign_keys = [f for f in self.fields if isinstance(f, ForeignKey)]
        self.sql = f'''
        CREATE TABLE {self.name} (
            {','.join([f.sql for f in self.fields.values()])},
            PRIMARY KEY ({','.join([f.name for f in self.fields.values() if f.is_pk])}){',' if len(foreign_keys)>0 else ''}
            {','.join([f'FOREIGN KEY ({f.name}) REFERENCES {f.ref_table}({f.ref_field})' for f in foreign_keys])}
        )'''

    def _exists(self) -> bool:
        sql = f"SELECT name FROM sqlite_schema WHERE name='{self.name}' AND type='table'"
        return len(list(self._cur.execute(sql))) > 0

    def Select(self, columns: list[str]|Literal['*'], unique:bool=False, where='') -> Iterator[tuple]:
        _columns = STAR if columns == STAR else ','.join([f for f in columns])
        sql = f"SELECT {'DISTINCT' if unique else ''} {_columns} FROM {self.name} {'WHERE' if where != '' else ''} {where}"
        return self._cur.execute(sql)

    def _insert_helper(self, fn, values):
        fn(F'''INSERT INTO {self.name} 
            ({','.join([f.name for f in self.fields.values()])})
            VALUES ({','.join(['?' for f in self.fields.values()])})
            ''', values)

    def _insert(self, values:tuple):
        assert len(values) == len(self.fields), f'expected {len(self.fields)} columns, got {len(values)}'
        self._insert_helper(self._cur.execute, values)

    def _insertMany(self, values:list[tuple]):
        self._insert_helper(self._cur.executemany, values)

class RegistryTable(Table):
    NAME='tables'
    TYPE='registry'
    def __init__(self, db: Database) -> None:
        self.F_table_name = Field('name', is_pk=True)
        self.F_fields = Field('fields', is_pk=True)
        self.F_type = Field('type', is_pk=False)
        self.db = db
        super().__init__(db, RegistryTable.NAME, [self.F_table_name, self.F_type, self.F_fields], type=RegistryTable.TYPE)

        if not self._exists():
            self._cur.execute(self.sql)

    def _register(self, table: Table):
        fields_json = jdumps([[f.name, f.is_pk, f.dtype.value, f.not_null] for f in table.fields.values()])
        self._insert((table.name, table.type, fields_json))

    def GetFields(self, table_name: str) -> list[Field]:
        fres = list(self.Select(['fields'], where=f"name='{table_name}'"))
        assert len(fres) > 0, f"{table_name} is not in the registry"
        fs = jloads(fres[0][0])
        return [Field(name, is_pk, dtype, not_null) for name, is_pk, dtype, not_null in fs]

    def GetTypes(self) -> list[str]:
        types = []
        for row in self._cur.execute(F"SELECT DISTINCT {self.F_type.name} FROM {self.name}"):
            types.append(row[0])
        return types

    def _getTables(self, where):
        tables = []
        for name, fields, t in self.Select([self.F_table_name.name, self.F_fields.name, self.F_type.name], where):
            fs = [Field(name, is_pk, DType[dtype], not_null) for name, is_pk, dtype, not_null in jloads(fields)]
            tables.append(Table(self.db, name, fs, t))
        return tables

    def GetTables(self, type:str|Literal['*']=STAR) -> list[Table]:
        where = '' if type == STAR else f'{self.F_type.name}="{type}"'
        return self._getTables(where)

    def GetTable(self, table_name: str) -> Table:
        where = f'{self.F_table_name.name}="{table_name}"'
        result = self._getTables(where)
        assert len(result) > 0 , f"{table_name} doesn't exist"
        return result[0]

    def HasTable(self, table_name: str) -> bool:
        where = f'{self.F_table_name.name}="{table_name}"'
        return len(self._getTables(where)) > 0

class TraceStep:
    def __init__(self, forward: bool, index: str, table_name: str) -> None:
        self.Forward = forward
        self.Index = index
        self.Table_name = table_name
        self.Tuple = (forward, index, table_name)

class SecondaryIndex(Table):
    NAME='si'
    TYPE='secondary_index'
    def __init__(self, db: Database) -> None:
        self.F_table_name = Field('table_name', is_pk=True)
        self.F_index_name = Field('index_name', is_pk=True)
        self.F_primary_key = Field('p_key', is_pk=True)
        self.F_secondary_key = Field('s_key', is_pk=True)
        self.db = db
        super().__init__(db, SecondaryIndex.NAME, [
            self.F_table_name,
            self.F_index_name,
            self.F_primary_key,
            self.F_secondary_key,
        ], type=SecondaryIndex.TYPE)

    def Add(self, table_name: str, data: dict, indexes:list[str]):
        entries = []
        def parse(v):
            return '_'.join(v) if isinstance(v, list) else str(v)
        for ind in indexes:
            for k, vals in data.items():
                if ind in vals:
                    entries += set([(table_name, ind, k, parse(v)) for v in vals[ind]])
                    # try:
                    #     self._insertMany()
                    # except Exception:
                    #     print(table_name, ind, k, vals[ind])
                        # exit(1)
        self._insertMany(entries)

class Database:
    def __init__(self, db_path: str, ext:str='db') -> None:
        toks = db_path.split('.')
        if toks[-1] != ext:
            toks.append(ext)
        db_path = '.'.join(toks)

        self._con = sqlite3.connect(db_path)
        self._cur = self._con.cursor()

        self.registry = RegistryTable(self)

        def create_if_not_exists(name, fields, ttype):
            r = self.registry
            if r.HasTable(name):
                return r.GetTable(name)
            else:
                self.secondary_index = self.MakeTable(name, fields, ttype)

        self.secondary_index = create_if_not_exists('si',[
            Field('table_name', is_pk=True),
            Field('index_name', is_pk=True),
            Field('p_key', is_pk=True),
            Field('s_key', is_pk=True),
        ], 'secondary_index')

        self.si_mappings = create_if_not_exists('sim', [
            Field('a', True),
            Field('b', True),
            Field('key', False),
        ], 'secondary_index_mappings')

        self.data_table_keys = create_if_not_exists('data_keys', [
            Field('key', is_pk=True),
            Field('table_name', is_pk=True),
        ])
        jk_name = 'data_keys'
        if r.HasTable(jk_name):
            self.secondary_index = r.GetTable(jk_name)
        else:
            self.secondary_index = self.MakeTable(si_name, [
                Field('table_name', is_pk=True),
                Field('index_name', is_pk=True),
                Field('p_key', is_pk=True),
                Field('s_key', is_pk=True),
            ], 'secondary_index')

        def onExit():
            self._con.close()
        atexit.register(onExit)

    def _addTable(self, table: Table):
        with self._con as con: # transaction
            con.execute(table.sql)
            self.registry._register(table)

    def MakeTable(self, name: str, fields: list[Field], type: str='') -> Table:
        table = Table(self, name, fields, type)
        self._addTable(table)
        return table

    def ImportTable(self, name: str, fields: list[Field], type: str, data: dict, secondary_indexes:list=list()):

        pass

    def GetTraceableAttributes(self):
        si = self.secondary_index
        # si.Select(si., True)

    def Trace(self, steps: list[TraceStep]) -> list[tuple[str, str]]:
        if len(steps) == 0: return []
        def makeSql(use_table_name):
            pk = 'p_key'
            sk = 's_key'
            def recurse(m, i):
                fwd, key, table = m[0]
                x = toLetters(i)

                if len(m) == 1:
                    return f"si AS {x}", f"{x}.index_name='{key}' and {x}.table_name='{table}'"

                f2, k2, t2 = m[1]
                y = toLetters(i+1)
                link = f"{x}.{sk if fwd else pk}={y}.{pk if f2 else sk}"
                if len(m) == 2:
                    joins = f"si AS {x} INNER JOIN si AS {y} ON {link}"
                    where = f"{x}.index_name='{key}' AND {y}.index_name='{k2}'"
                    if use_table_name: where += f"AND {x}.table_name='{table}' AND {y}.table_name='{t2}'"
                else:
                    pjoins, pwhere = recurse(m[1:], i+1)
                    joins = f"si AS {x} INNER JOIN ({pjoins}) ON {link}"
                    where = f"{x}.index_name='{key}' AND {pwhere}"
                    if use_table_name: where += f"AND {x}.table_name='{table}'"
                    where += f"AND {pwhere}"
                return joins, where

            j, w = recurse([ts.Tuple for ts in steps], 1)
            ka = pk if steps[0].Forward else sk
            kb = pk if not steps[-1].Forward else sk
            return f"SELECT {toLetters(1)}.{ka}, {toLetters(len(steps))}.{kb} FROM ({j}) WHERE {w}"
        
        use_table_names = len(set([ts.Index for ts in steps])) != len(steps) # if indexes are not sufficiently unique
        sql = makeSql(use_table_names)
        return list(self._cur.execute(sql))

    def Commit(self):
        self._con.commit()


