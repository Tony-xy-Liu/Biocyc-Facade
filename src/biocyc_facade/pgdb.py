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
    VER = '1.0'

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path, ext=Pgdb.EXT)

def ImportFromBiocyc(db_path: str, flat_files: str, silent=False) -> Pgdb:
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
    i_list = [(k, v[0] if len(v)==1 else jdumps(v)) for k, v in info_data.items()]
    i_list.append(('Kernel_version', Pgdb.VER))
    assert len(i_list)>0, f'no info files!'
    db.info._insertMany(i_list)

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
