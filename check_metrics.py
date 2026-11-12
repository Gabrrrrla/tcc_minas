import sys, json
d = json.load(sys.stdin)
for r in d['data']['result']:
    dev = r['metric'].get('device', '?')
    if 'ogstun' in dev:
        print(dev, r['value'][1])
