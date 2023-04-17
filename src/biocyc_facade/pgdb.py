from __future__ import annotations
from pathlib import Path
import sys
import os
import json

from .models.sql import STAR, Database, Field, SecondaryIndex as SI, Dat as sqlDat, TraceStep, TraceResult, Traceable as sqlTraceable
from .utils import dictAppend, parseDat, jdumps, jloads

class Dat(sqlDat):
    PROTEINS = 'proteins.dat'
    REACTIONS = 'reactions.dat'
    ENZYMES = 'enzrxns.dat'
    PATHWAYS = 'pathways.dat'
    GENES = 'genes.dat'
    REGULATION = 'regulation.dat'
    COMPOUNDS = 'compounds.dat'
    PROTEIN_FEATURES = 'protein-features.dat'
    PRO_LIGAND_CPLX = 'protligandcplxes.dat'
    RNAS = 'rnas.dat'
    SPECIES = 'species.dat'

class Traceable(sqlTraceable):
    PROT_TYPES = 0
    PROT_COMMON_NAME = 1
    PROT_DB_LINKS = 2
    CMPD_TYPES = 3
    CMPD_COMMON_NAME = 4
    RXN_TYPES = 5
    RXN_COMMON_NAME = 6
    RXN_EC = 7
    CMPD_REACTANTS = 8
    CMPD_PRODUCTS = 9
    ENZ_COMMON_NAME = 10
    PATH_TYPES = 11
    PATH_COMMON_NAME = 12
    GENE_DB_LINKS = 13
    GENE_COMMON_NAME = 14
    PROTEINS = Dat.PROTEINS
    COMPOUNDS = Dat.COMPOUNDS
    REACTIONS = Dat.REACTIONS
    ENZYMES = Dat.ENZYMES
    PATHWAYS = Dat.PATHWAYS
    GENES = Dat.GENES


Dat.PROTEINS.SetSIs([
    ('TYPES', Traceable.PROT_TYPES),
    ('COMMON-NAME', Traceable.PROT_COMMON_NAME),
    ('DBLINKS', Traceable.PROT_DB_LINKS),
])
Dat.COMPOUNDS.SetSIs([
    ('TYPES', Traceable.CMPD_TYPES),
    ('COMMON-NAME', Traceable.CMPD_COMMON_NAME)
])
Dat.REACTIONS.SetSIs([
    ('TYPES', Traceable.RXN_TYPES),
    ('COMMON-NAME', Traceable.RXN_COMMON_NAME),
    ('EC-NUMBER', Traceable.RXN_EC),
    ('LEFT', Traceable.CMPD_REACTANTS),
    ('RIGHT', Traceable.CMPD_PRODUCTS),
])
Dat.ENZYMES.SetSIs([
    ('COMMON-NAME', Traceable.ENZ_COMMON_NAME),
    ('REACTION', Traceable.REACTIONS, 'E->REACTIONS'),
    ('ENZYME', Traceable.PROTEINS, 'E->PROTEINS'),
])
Dat.PATHWAYS.SetSIs([
    ('TYPES', Traceable.PATH_TYPES),
    ('COMMON-NAME', Traceable.PATH_COMMON_NAME),
    ('REACTION-LIST', Traceable.REACTIONS, 'W->REACTIONS'),
])
Dat.GENES.SetSIs([
    ('DBLINKS', Traceable.GENE_DB_LINKS),
    ('COMMON-NAME', Traceable.GENE_COMMON_NAME),
    ('PRODUCT', Traceable.PROTEINS, 'G->PROTEINS'),
])

class Pgdb(Database):
    EXT = 'pgdb'
    VER = '1.3'

    def __init__(self, db_path: str|Path) -> None:
        super().__init__(db_path, ext=Pgdb.EXT)
        self._mappings = {}

    def _performTrace(self, steps: list[TraceStep], intermediates=False) -> TraceResult:
        def _get_mapping_k(step: TraceStep):
            table, key = step.secondary_index.table_name, step.secondary_index.original_key
            return table, key

        def _add_mapping(step: TraceStep):
            _, k, table_name = step.tuple
            table = self.registry.GetTable(self._si_of_table(table_name))
            r_map, map = {}, {}
            for my_k, target in table.Select([self.SI_KEY, self.SI_TARGET], where=f"{self.SI_NAME}='{k}'", unique=True):
                r_map[my_k] = r_map.get(my_k, []) + [target]
                map[target] = map.get(target, []) + [my_k]
            
            self._mappings[_get_mapping_k(step)] = map, r_map

        maps = []
        for step in steps:
            if step.secondary_index not in self._mappings: _add_mapping(step)
            f_map, r_map = self._mappings[_get_mapping_k(step)]
            map = f_map if step.forward else r_map
            maps.append(map)

        def _get_children(key: str|None, depth: int) -> set[str]|set[None]:
            # allow for self_referential steps
            children = set()
            to_check: list = list(maps[depth].get(key, []))
            while len(to_check) > 0:
                child = to_check.pop()
                children.add(child)
                additional = maps[depth].get(child, [])
                to_check += [c for c in additional if c not in children]
            # children = set(to_check)
            return children if len(children) > 0 else set([None])

        c = 0
        def _trace(key:str|None, depth: int) -> list[tuple]:
            if depth >= len(maps): return [(key,)]
            children = _get_children(key, depth)
            return [(key,)+rest for g in [_trace(child, depth+1) for child in children] for rest in g]
            
        # first_keys = [k for g in [_get_children(ch, 0) for ch in maps[0]] for k in g]
        results = [trace for g in [_trace(key, 0) for key in maps[0]] for trace in g]
        if not intermediates:
            results = list(set([(t[0], t[-1]) for t in results if t[-1] is not None]))

        sql = "* used dictionaries instead"
        return TraceResult(results, steps, sql)

    def _insert_info(self, info_data: dict):
        for k in list(info_data):
            if k.upper() != k: del info_data[k] # remove previous custom fields
        info_data['biocyc_facade_ver'] = Pgdb.VER
        i_list = [(k, v[0] if len(v)==1 else jdumps(v)) for k, v in info_data.items()]
        assert len(i_list)>0, f'no info files!'
        self.info._insert_many(i_list)

    @classmethod
    def ImportFromBiocyc(cls, db_path: str, flat_files: str, silent=False) -> Pgdb:
        if flat_files[-1] == '/': flat_files = flat_files[:-1] 
        assert not os.path.isfile(db_path), f'can not import into {db_path} since it already exists'
        
        db = Pgdb(db_path)

        # ==========================================================
        # get version info

        VER_DAT = 'version.dat'
        INFO_DATS = [VER_DAT, 'species.dat']
        info_data = {}
        for info_file in INFO_DATS:
            path = f'{flat_files}/{info_file}'
            if not os.path.isfile(path): continue
            
            if info_file == VER_DAT:
                with open(path) as ver:
                    for line in ver:
                        if line[:2] in [';;', '//'] or line.startswith('#'): continue
                        toks = line[:-1].split('\t')
                        if len(toks) != 2:
                            print(f'unrecognized line in {info_file} [{line}]')
                            continue
                        k, v = toks
                        dictAppend(info_data, k, v)
                    
            else:
                idat = parseDat(path, 'UNIQUE-ID', {}, all_fields=True)
                uid, data = list(idat.items())[0]
                info_data['UNIQUE-ID'] = uid
                for k, v in data.items():
                    info_data[k] = v
        db._insert_info(info_data)

        # ==========================================================
        # load dats

        counts = 0
        for edat in Dat:
            dat_file: str = edat.file
            path = f'{flat_files}/{dat_file}'
            if not os.path.isfile(path):
                if not silent: print(f'skipping {dat_file}, not found')
                continue

            pdat = parseDat(path, 'UNIQUE-ID', {
                'DBLINKS': lambda x: x[1:-1].replace('"', '').split(' ')[:2],
                'GIBBS-0': lambda x: x.strip(),
                'MOLECULAR-WEIGHT-EXP': lambda x: x.strip(),
            }, all_fields=True)
            counts += len(pdat)
            if len(pdat) == 0:
                if not silent: print(f'{dat_file} was empty')
            else:
                db.ImportDataTable(edat.table_name, pdat, edat.secondary_indexes, silent)

        db.info._insert(('Total_entries', counts))

        db.Commit()
        return db

    def UpdateSchema(self, new_db_path: str, silent=False, _overwrite=False) -> Pgdb:
        if not _overwrite:
            assert not os.path.isfile(new_db_path), f'can not import into {new_db_path} since it already exists'
        elif os.path.exists(new_db_path):
            print(f"OVERWRITING {new_db_path} !")
            os.unlink(new_db_path)
        
        db = Pgdb(new_db_path)

        # ==========================================================
        # get version info
        info_data = self.GetInfo()
        count_k = 'Total_entries'
        if count_k in info_data: del info_data[count_k]
        db._insert_info(info_data)

        # ==========================================================
        # load dats

        counts = 0
        for edat in Dat:
            try:
                pdat = self.GetDataTable(edat)
                counts += len(pdat)
            except AssertionError:
                if not silent: print(f"{edat} didn't exist!")
                continue
            db.ImportDataTable(edat.table_name, pdat, edat.secondary_indexes, silent)

        db.info._insert(('Total_entries', counts))
        db.Commit()
        return db
