from __future__ import annotations
import sys
import os
import json
from enum import Enum

from biocyc_facade.models.sql import STAR, Database, DType, Field, Table, ForeignKey
from .utils import dictAppend, parseDat, jdumps, jloads

class Dat(Enum):
    PROTEINS = 'proteins.dat', ['DBLINKS']
    ENZRXNS = 'enzrxns.dat', ['REACTION', 'ENZYME']
    PATHWAYS = 'pathways.dat', ['REACTION-LIST']
    GENES = 'genes.dat'
    REGULATION = 'regulation.dat'
    REACTIONS = 'reactions.dat', ['EC-NUMBER']
    COMPOUNDS = 'compounds.dat'
    PROTEIN_FEATURES = 'protein-features.dat'
    PRO_LIGAND_CPLX = 'protligandcplxes.dat'
    RNAS = 'rnas.dat'
    SPECIES = 'species.dat'

    def __init__(self, val: str, secondary_indexes: list[str]=list()) -> None:
        super().__init__()
        self.file = val
        self.table_name = val.replace('.', '_').replace('-', '_')
        self.secondary_indexes = secondary_indexes

# class Mapping(Enum):
#     DB_PROT =       'proteins_dat',    'dblinks_proteins_map', 'DBLINKS',              'proteins_dat'
#     PROT_ENZRXN =   'enzrxns_dat',     'protein_enzrxn_map',   'ENZYME',               'proteins_dat'
#     ENZRXN_RXN =    'reactions_dat',   'enzrxn_reaction_map',  'ENZYMATIC-REACTION',   'enzrxns_dat'
#     RXN_PATHWAY =   'pathways_dat',    'reaction_pathway_map', 'REACTION-LIST',        'reactions_dat'
#     def __init__(self, to: str, table: str, via: str, frm: str) -> None:
#         super().__init__()
#         self.table_name = table
#         self.map_from = frm
#         self.map_to = to
#         self.map_key = via 

class Pgdb:
    EXT = 'pgdb'
    SCHEMA_VER = 1

    def __init__(self, db_path: str) -> None:
        db = Database(db_path, ext=Pgdb.EXT)        
        self._database = db
        self.registry = db.registry

        T_INFO = 'info'
        T_JKOD = 'json_keys_of_dats'
        if self.registry.HasTable(T_INFO):
            self.info = db.registry.GetTable(T_INFO)
            self.json_keys_of_dats = db.registry.GetTable(T_JKOD)
        else:
            META = 'metadata'
            self.info = db.MakeTable(T_INFO, [
                Field('key', is_pk=True),
                Field('val'),
            ], type=META)

            self.json_keys_of_dats = db.MakeTable(T_JKOD, [
                Field('key', is_pk=True),
                ForeignKey(Field('table_name', is_pk=True), db.registry, db.registry.F_table_name),
            ], type=META)

            SV = 'SCHEMA_VER'
            self.info._insert((SV, Pgdb.SCHEMA_VER))
            self._database._con.commit()

    def GetInfo(self):
        info = {}
        for k, v in self.info.Select(STAR):
            info[k] = v
        return info

    def ListEntries(self, dat: Dat) -> list[str]:
        table = self.registry.GetTable(dat.table_name)
        return [i[0] for i in table.Select(['id'])]

    def GetEntry(self, dat: Dat, id: str) -> dict:
        table = self.registry.GetTable(dat.table_name)
        res = list(table.Select(['json'], where=f"id='{id}'"))
        assert len(res) > 0, f"{id} not in {dat.value}"
        return jloads(res[0][0])

    def GetDat(self, dat: Dat) -> dict:
        table = self.registry.GetTable(dat.table_name)
        fields = table.fields.values()
        pks = [f.name for f in fields if f.is_pk]
        data = {}
        for entry in table.Select(pks+['json']):
            j = entry[-1]
            k = '_'.join(entry[:-1])
            v = jloads(j)
            data[k] = v
        return data

    def GetDatFields(self, dat: Dat) -> set[str]:
        return set([x[0] for x in self.registry.GetTable('json_keys_of_dats').Select(['key'], where=f"table_name='{dat.table_name}'")])

    # def _makeMappingPlan(self, source: Dat, dest: Dat):
    #     def usekey(key):
    #         return lambda v: v.get(key, list())
    #     passthrough = lambda v: v

    #     # from: (to, table, method)
    #     RELATIONS = {
    #         Dat.PATHWAYS: [
    #             (Dat.REACTIONS, Dat.PATHWAYS, usekey('REACTION-LIST')),
    #         ],
    #         Dat.REACTIONS: [
    #             (Dat.PATHWAYS, Mapping.RXN_PATHWAY, passthrough),
    #             (Dat.ENZRXNS, Dat.REACTIONS, usekey('ENZYMATIC-REACTION')),
    #         ],
    #         Dat.ENZRXNS: [
    #             (Dat.REACTIONS, Dat.ENZRXNS, usekey('REACTION')),
    #             (Dat.PROTEINS, Dat.ENZRXNS, usekey('ENZYME')),
    #         ],
    #         Dat.PROTEINS: [
    #             (Dat.ENZRXNS, Mapping.PROT_ENZRXN, passthrough),
    #         ]
    #     }

    #     def find(curr, path):
    #         if curr == dest:
    #             return path
    #         for to, table, fn in RELATIONS.get(curr, []):
    #             find(to, path+[(table, fn)])
    #         return None

    #     path = find(source, [])
    #     assert path is not None, f"no known way to get from {source.name} to {dest.name}"
    #     for t, fn in path:
    #         pass

    # def MapEntries(self, source: Dat, id: str, dest: Dat):
    #     pass

    # def GetMapping(self, map: Mapping) -> dict[str, list[str]]:
    #     table = self.registry.GetTable(map.table_name)
    #     fields = table.fields.values()
    #     pks = [f.name for f in fields if f.is_pk]
    #     mapping = {}
    #     for entry in table.Select(pks+['found_in']):
    #         v = entry[-1]
    #         k = '_'.join(entry[:-1])
    #         dictAppend(mapping, k, v)
    #     return mapping

def ImportFromBiocyc(db_path: str, flat_files: str) -> Pgdb:
    if flat_files[-1] == '/': flat_files = flat_files[:-1] 
    assert not os.path.isfile(db_path), f'can not import into {db_path} since it already exists'
    
    db = Pgdb(db_path)
    json_keys_of_dats = db.json_keys_of_dats

    # ==========================================================
    # get version info

    VER_DAT = 'version.dat'
    version_data = {}
    with open(f'{flat_files}/{VER_DAT}') as ver:
        for line in ver:
            if line.startswith(';;') or line.startswith('//'): continue
            toks = line[:-1].split('\t')
            if len(toks) != 2:
                print(f'unrecognized line in {VER_DAT} [{line}]')
                continue
            k, v = toks
            dictAppend(version_data, k, v)
        version_data = [(k, v[0] if len(v)==1 else jdumps(v)) for k, v in version_data.items()]
    
    assert len(version_data)>0, f'{VER_DAT} is empty!'
    db.info._insertMany(version_data)

    # ==========================================================
    # load dats

    DAT_TYPE = 'dat'
    all_dats = {}
    dat_tables: dict[str, Table] = {}
    for edat in Dat:
        dat_file: str = edat.file
        print(f"loading {dat_file}")
        
        pdat = parseDat(f'{flat_files}/{dat_file}', 'UNIQUE-ID', {
            'DBLINKS': lambda x: x[1:-1].replace('"', '').split(' ')[:2],
            'GIBBS-0': lambda x: x.strip(),
            'MOLECULAR-WEIGHT-EXP': lambda x: x.strip(),
        }, all_fields=True)

        dat_table = db._database.MakeTable(edat.table_name, [
            Field('id', is_pk=True),
            Field('json')
        ], type = DAT_TYPE)

        # just using a dict because not enough useful links between tables to justify
        entries: list[tuple] = []
        json_keys = set()
        for k, val in pdat.items():
            entries.append((k, json.dumps(val, separators=(',', ':'))))
            json_keys = json_keys.union(set(val.keys()))

        dat_table._insertMany(entries)
        json_keys_of_dats._insertMany([(k, edat.table_name) for k in json_keys])
        db._database.secondary_index.Add(edat.table_name, pdat, edat.secondary_indexes)
        all_dats[edat.table_name] = pdat
        dat_tables[edat.table_name] = dat_table

    # # ==========================================================
    # # make reverse mappings, like aws dynamo's secondary indexes

    # def makeRev(mapping, revKey, parser):
    #     rev = {}
    #     for k, v in mapping.items():
    #         if revKey not in v: continue
    #         for val in v[revKey]:
    #             parsed = parser(val)
    #             if type(parsed) == list:
    #                 [dictAppend(rev, p, k) for p in parsed]
    #             else:
    #                 dictAppend(rev, parsed, k)
    #     return rev

    # def linearize(mapping):
    #     entries = set()
    #     for k, vs in mapping.items():
    #         for v in vs:
    #             if type(k) == tuple:
    #                 db, id = k
    #                 entries.add((db, id, v))
    #             else:
    #                 entries.add((k, v))
    #     return list(entries)

    # # source table, mapping name, target id, target table
    # for mapping in Mapping:
    #     source, name, target_id, target  = mapping.value
    #     print(f'mapping {target} to {source} via {target_id}')
    #     dat_table = dat_tables[target]
    #     fn = lambda x: x
    #     fields = [
    #         Field('id', is_pk=True),
    #         ForeignKey(Field('found_in', is_pk=True), dat_table, dat_table.fields['id'])
    #     ]
    #     if name == Mapping.DB_PROT.table_name:
    #         fields = [Field('db', is_pk=True)] + fields
    #         fn = lambda x: tuple(x)
    #     mapping = makeRev(all_dats[source], target_id, fn)
    #     table = Table(db._database, name, fields)
    #     table._insertMany(linearize(mapping))

    db._database.Commit()
    return db