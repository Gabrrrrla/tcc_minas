var doc = db.subscribers.findOne({imsi: '001010000000001'});
print('--- FULL SLICE ARRAY ---');
printjson(doc.slice);
