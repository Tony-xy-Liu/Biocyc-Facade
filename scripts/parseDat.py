from typing import Callable

def passfn(x):
    return x

# if parser (Callable) returns false, skip
def parse(fpath: str, key: str, parsers: dict[str, Callable], all_fields: bool = False) -> dict:
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