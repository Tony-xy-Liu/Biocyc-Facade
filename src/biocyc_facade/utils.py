import json
from typing import Any, Callable

def jdumps(obj: Any):
    return json.dumps(obj, separators=(',', ':'))

jloads = json.loads

def dictAppend(d:dict, k, v):
    arr = d.get(k, list())
    arr.append(v)
    d[k] = arr

def toLetters(i: int):
    base=26
    off = 97
    c = lambda x: chr(x+off-1) if x != 0 else 'z'
    if i > base-1:
        next = (i//base)*base
        rem = i - next
        print(next, rem)
        return f'{toLetters(next//base)}{c(rem)}'
    else:
        return f'{c(i)}'

def passfn(x):
    return x

# not all biocyc .dat files are formatted the same !!!
# if parser (Callable) returns false, skip
def parseDat(fpath: str, key: str, parsers: dict[str, Callable], all_fields: bool = False) -> dict:
    file = open(fpath, encoding='latin-1')

    parsed = {}
    def pushItem():
        nonlocal item
        ik = item[key][0]
        del item[key]
        parsed[ik] = item
        item = {}

    def addField(f, v):
        if not v: return
        nonlocal item
        if f in item:
            data = item[f]
            data.append(v)
        else:
            item[f] = [v]

    item = {}
    def parseField(entry: str):
        tok = entry.split(' - ')
        field, value = tok[0], ' - '.join(tok[1:])
        
        if len(item)>0 and field == key:
            pushItem()

        if field == key:
            addField(field, value[:-1])
        else:
            if field in parsers:
                parser = parsers[field]
            elif not all_fields:
                return
            else:
                parser = passfn 
            addField(field, parser(value[:-1]))

    entry = ''
    while 1:
        line = file.readline()
        if not line: # end
            parseField(entry)
            pushItem()
            break
        if line.startswith('#'): continue
        if line.startswith('//'): continue
        if not line.startswith('/'):
            parseField(entry)
            entry = ''

        entry += line
        
    file.close()
    return parsed