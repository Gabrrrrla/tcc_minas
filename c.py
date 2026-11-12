import json, glob, os

pasta = 'lab-data'
arquivos = sorted(glob.glob(os.path.join(pasta, '*', 'prometheus', 'sst2_ogstun2_transmit.json')))
if not arquivos:
    print('nenhum arquivo encontrado')
else:
    f = arquivos[-1]
    print('arquivo:', f)
    d = json.load(open(f))
    vals = [float(v[1]) for v in d['data']['result'][0]['values'] if float(v[1]) > 1]
    if vals:
        print('max=%.1f media=%.1f n=%d' % (max(vals), sum(vals)/len(vals), len(vals)))
    else:
        print('sem trafego')
