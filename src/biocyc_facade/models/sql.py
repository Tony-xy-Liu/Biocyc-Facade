from __future__ import annotations
import sqlite3
import atexit
from enum import Enum
from typing import Iterator, Literal, Any

from ..utils import dictAppend, jdumps, jloads, toLetters
class SecondaryIndex:
    def __init__(self, table: str, key: str, target: str, key_in_index: str='') -> None:
        """specifying a unique @key_in_index will make tracing much faster"""
        if key_in_index == '': key_in_index = key
        self.key = key_in_index
        self.table_name = table
        self.index_name = key
        self.target_name = target

class TraceStep:
    def __init__(self, forward: bool, index: SecondaryIndex) -> None:
        self.forward = forward
        self.secondary_index = index
        self.index_name = index.index_name
        self.table_name = index.table_name
        self.tuple = (forward, self.index_name, self.table_name)

class TraceResult:
    def __init__(self, result: list[tuple[str, str]], steps: list[TraceStep]) -> None:
        self.results = result
        self.steps = steps
        self.keys_used = [s.index_name for s in steps]
        self.is_empty = len(self.results) == 0

    def __iter__(self):
        return self.results.__iter__()
    def __getitem__(self, i):
        return self.results[i]

class _dat_statics:
    fwd = {}
    rev = {}
    from_table = {}

class Dat(Enum):
    def __init__(self, val: str) -> None:
        super().__init__()
        self.file = val
        self.table_name = val.replace('.', '_').replace('-', '_')
        self.secondary_indexes: list[SecondaryIndex] = list()
        _dat_statics.from_table[self.table_name] = self

    @classmethod
    def GetSILinks(cls):
        return dict(_dat_statics.fwd), dict(_dat_statics.rev)

    @classmethod
    def FromTableName(cls, table_name: str) -> Dat:
        return _dat_statics.from_table.get(table_name, None)

    def SetSIs(self, secondary_indexes: list[tuple]=list()):
        def makesi(t):
            k, targ = t[:2]
            si = SecondaryIndex(self.table_name, k, str(targ))
            if len(t) == 3: si.key = t[2] # added alt_key
            return si

        self.secondary_indexes = list()
        for tup in secondary_indexes:
            si = makesi(tup)
            dictAppend(_dat_statics.fwd, self.table_name, si.target_name, unique=True)
            dictAppend(_dat_statics.rev, si.target_name, self.table_name, unique=True)
            self.secondary_indexes.append(si)



    def __str__(self) -> str:
        return self.table_name

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

        # foreign_keys = [f for f in self.fields if isinstance(f, ForeignKey)]
        self.sql = f'''
        CREATE TABLE {self.name} (
            {','.join([f.sql for f in self.fields.values()])},
            PRIMARY KEY ({','.join([f.name for f in self.fields.values() if f.is_pk])})
        )'''

    def _exists(self) -> bool:
        sql = f"SELECT name FROM sqlite_schema WHERE name='{self.name}' AND type='table'"
        return len(list(self._cur.execute(sql))) > 0

    def Select(self, columns: str|list[str]|Literal['*'], unique:bool=False, where='') -> Iterator[tuple]:
        if isinstance(columns, str): columns = [columns]
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
        fres = list(self.Select('fields', where=f"name='{table_name}'"))
        assert len(fres) > 0, f"{table_name} is not in the registry"
        fs = jloads(fres[0][0])
        return [Field(name, is_pk, dtype, not_null) for name, is_pk, dtype, not_null in fs]

    def GetTypes(self) -> list[str]:
        types = []
        for row in self._cur.execute(F"SELECT DISTINCT {self.F_type.name} FROM {self.name}"):
            types.append(row[0])
        return types

    def _getTables(self, where: str):
        tables = []
        for name, fields, t in self.Select([self.F_table_name.name, self.F_fields.name, self.F_type.name], False, where):
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

class Database:
    DATA_KEY = 'key'
    DATA_TYPE = 'json'
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
                return self.AttachTable(name, fields, ttype)

        self.secondary_index = create_if_not_exists('si',[
            Field('table_name', is_pk=True),
            Field('index_name', is_pk=True), # todo: split into multiple tables
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
        ], 'data_table_keys')

        def onExit():
            self._con.close()
        atexit.register(onExit)

    def _addTable(self, table: Table):
        with self._con as con: # transaction
            con.execute(table.sql)
            self.registry._register(table)

    def AttachTable(self, name: str, fields: list[Field], type: str='') -> Table:
        table = Table(self, name, fields, type)
        self._addTable(table)
        return table

    def ImportDataTable(self, name: str, data: dict, secondary_indexes:list[SecondaryIndex]=list()):
        print(f"loading [{name}]")
        for si in secondary_indexes:
            print(f"\t secondary index: {si.index_name}")

        table = self.AttachTable(name, [
            Field(self.DATA_KEY, is_pk=True),
            Field(self.DATA_TYPE)
        ], self.DATA_TYPE)

        # for si where reference is tuple (DBLINKS, for ex)
        def parse(v):
            return '_'.join(v) if isinstance(v, list) else str(v)

        # just using a dict because not enough useful links between tables to justify
        entries: list[tuple] = []
        json_keys = set()
        sis: list[tuple] = []
        for k, val in data.items():
            entries.append((k, jdumps(val)))
            json_keys = json_keys.union(set(val.keys()))
            for si in secondary_indexes:
                sis += [(name, si.index_name, k, parse(v)) for v in val.get(si.key, list())]

        table._insertMany(entries)
        self.secondary_index._insertMany(list(set(sis)))
        self.si_mappings._insertMany([(name, si.target_name, si.key) for si in secondary_indexes])
        self.data_table_keys._insertMany([(k, name) for k in json_keys])
        return table

    def ListEntries(self, table_name: Dat|str):
        table = self.registry.GetTable(str(table_name))
        return [i[0] for i in table.Select(self.DATA_KEY)]

    def GetKeysOfDataTable(self, table_name: str) -> set[str]:
        return set([x[0] for x in self.data_table_keys.Select(self.DATA_KEY, where=f"table_name='{table_name}'")])

    def GetEntry(self, table_name: Dat|str, key: str):
        table = self.registry.GetTable(str(table_name))
        res = list(table.Select(self.DATA_TYPE, where=f"{self.DATA_KEY}='{key}'"))
        assert len(res) > 0, f"[{key}] not in {table_name}"
        return jloads(res[0][0])

    def GetDataTable(self, table_name: Dat|str):
        table = self.registry.GetTable(str(table_name))
        assert table.type == self.DATA_TYPE, \
            f"[{table}] has type [{table.type}] is not a data table\nuse the registry instead"
        fields = table.fields.values()
        pks = [f.name for f in fields if f.is_pk]
        data = {}
        for entry in table.Select(pks+[self.DATA_TYPE]):
            j = entry[-1]
            k = '_'.join(entry[:-1])
            v = jloads(j)
            data[k] = v
        return data

    def GetTraceableAttributes(self):
        return self.si_mappings.Select('key', True)

    def Trace(self, steps: list[TraceStep]) -> TraceResult:
        if len(steps) == 0: return TraceResult([], steps)
        def makeSql(use_table_name):
            pk = 'p_key'
            sk = 's_key'
            def recurse(m, i):
                fwd, key, table = m[0]
                x = toLetters(i)

                if len(m) == 1:
                    return f"si AS {x}", f"{x}.index_name='{key}'" \
                        + (" AND {x}.table_name='{table}'" if use_table_name else "")

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
                    where = f"{x}.index_name='{key}'"
                    if use_table_name: where += f"AND {x}.table_name='{table}'"
                    where += f" AND {pwhere}"
                return joins, where

            j, w = recurse([ts.tuple for ts in steps], 1)
            ka = pk if steps[0].forward else sk
            kb = pk if not steps[-1].forward else sk
            return f"SELECT {toLetters(1)}.{ka}, {toLetters(len(steps))}.{kb} FROM ({j}) WHERE {w}"
        
        use_table_names = len(set([ts.index_name for ts in steps])) != len(steps) # if indexes are not sufficiently unique
        sql = makeSql(use_table_names)
        results: list[tuple[str, str]] = list(self._cur.execute(sql))
        return TraceResult(results, steps)

    def Commit(self):
        self._con.commit()


