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
Dat.COMPOUNDS.SetSIs([
    ('COMMON-NAME', 'C-COMMON-NAME')
])
Dat.REACTIONS.SetSIs([
    ('EC-NUMBER', 'EC-NUMBER'),
    ('COMMON-NAME', 'R-COMMON-NAME'),
    ('LEFT', 'REACTANTS'),
    ('RIGHT', 'PRODUCTS'),
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
    ('DBLINKS', 'G-DBLINKS'),
    ('COMMON-NAME', 'G-COMMON-NAME'),
])

class Pgdb(Database):
    EXT = 'pgdb'
    SCHEMA_VER = '1.0'

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, ext=Pgdb.EXT)  

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
        vd_list = [(k, v[0] if len(v)==1 else jdumps(v)) for k, v in version_data.items()]
        vd_list.append(('Schema_version', Pgdb.SCHEMA_VER))
    
    assert len(vd_list)>0, f'{VER_DAT} is empty!'
    db.info._insertMany(vd_list)

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