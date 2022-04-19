from __future__ import annotations
import sys
import os
import json
from enum import Enum

from biocyc_facade.models.sql import STAR, Database, Field, SecondaryIndex as SI, Dat as sqlDat, TraceStep
from .utils import dictAppend, parseDat, jdumps, jloads

class Dat(sqlDat):
    PROTEINS = 'proteins.dat'
    REACTIONS = 'reactions.dat'
    ENZRXNS = 'enzrxns.dat'
    PATHWAYS = 'pathways.dat'
    GENES = 'genes.dat'
    REGULATION = 'regulation.dat'
    COMPOUNDS = 'compounds.dat'
    PROTEIN_FEATURES = 'protein-features.dat'
    PRO_LIGAND_CPLX = 'protligandcplxes.dat'
    RNAS = 'rnas.dat'
    SPECIES = 'species.dat'

Dat.PROTEINS.SetSIs([
    ('DBLINKS', 'P-DBLINKS'),
    ('COMMON-NAME', 'P-COMMON-NAME'),
])
Dat.REACTIONS.SetSIs([
    ('EC-NUMBER', 'EC-NUMBER'),
    ('COMMON-NAME', 'R-COMMON-NAME'),
])
Dat.ENZRXNS.SetSIs([
    ('REACTION', Dat.REACTIONS, 'E->REACTIONS'),
    ('ENZYME', Dat.PROTEINS),
    ('COMMON-NAME', 'E-COMMON-NAME'),
])
Dat.PATHWAYS.SetSIs([
    ('REACTION-LIST', Dat.REACTIONS, 'W->REACTIONS'),
    ('COMMON-NAME', 'W-COMMON-NAME'),
])
Dat.GENES.SetSIs([
    ('DBLINKS', 'DBLINKS', 'G-DBLINKS'),
    ('COMMON-NAME', 'G-COMMON-NAME'),
])

class Pgdb(Database):
    EXT = 'pgdb'
    SCHEMA_VER = 1

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, ext=Pgdb.EXT)  

        T_INFO = 'info'
        if self.registry.HasTable(T_INFO):
            self.info = self.registry.GetTable(T_INFO)
        else:
            self.info = self.AttachTable(T_INFO, [
                Field('key', is_pk=True),
                Field('val'),
            ], type='metadata')

            SV = 'SCHEMA_VER'
            self.info._insert((SV, Pgdb.SCHEMA_VER))
            self._con.commit()

    def GetInfo(self):
        info = {}
        for k, v in self.info.Select(STAR):
            info[k] = v
        return info

    def Trace(self, source: Dat|str, target: Dat|str):
        links, rev_links = Dat.GetSILinks()

        def getDat(dat):
            if dat in set(item.value for item in Dat):
                return Dat(dat)
            return dat

        t_str = str(target)
        def search(curr, path, dirs):
            if curr == t_str: return path+[curr], dirs
            if curr in path: return None

            nexts:list[tuple[str, bool]] = [(l, True) for l in links.get(curr, [])]
            nexts += [(l, False) for l in rev_links.get(curr, [])]
            for n, fwd in nexts:
                res = search(n, path+[curr], dirs+[fwd])
                if res is not None: return res
            return None
        res = search(str(source), [], [])
        
        assert res is not None, f"no conversion found between [{source}] and [{target}]"
        path, dirs = res
        trace = []
        for i, (p, d) in enumerate(zip(path, dirs)):
            dat = Dat.FromTableName(p if d else path[i+1])
            # gets the matching set of p, p+1 in dat.si
            si = dict((str(sorted((s.table_name, s.target_name))), s) for s in dat.secondary_indexes)
            k = str(sorted((p, path[i+1])))
            if k not in si:
                print(k)
                print(si)
            si = si[k]
            trace.append(TraceStep(d, si))
        return super().Trace(trace)

def ImportFromBiocyc(db_path: str, flat_files: str) -> Pgdb:
    if flat_files[-1] == '/': flat_files = flat_files[:-1] 
    assert not os.path.isfile(db_path), f'can not import into {db_path} since it already exists'
    
    db = Pgdb(db_path)

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

    counts = 0
    for edat in Dat:
        dat_file: str = edat.file
        
        pdat = parseDat(f'{flat_files}/{dat_file}', 'UNIQUE-ID', {
            'DBLINKS': lambda x: x[1:-1].replace('"', '').split(' ')[:2],
            'GIBBS-0': lambda x: x.strip(),
            'MOLECULAR-WEIGHT-EXP': lambda x: x.strip(),
        }, all_fields=True)
        counts += len(pdat)

        db.ImportDataTable(edat.table_name, pdat, edat.secondary_indexes)

    db.info._insert(('Total_entries', counts))

    db.Commit()
    return db