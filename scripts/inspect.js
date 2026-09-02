var doc = db.subscribers.findOne({imsi: '999700000000001'});
print('--- FULL SLICE ARRAY ---');
printjson(doc.slice);
