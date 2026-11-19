const planes = [{ callsign: 'UAL1310', lat: 37.4, lng: -122.1 }, { callsign: 'DAL1', lat: 33.6, lng: -84.4 }, { callsign: 'AAL100', lat: 40.6, lng: -73.8 }, { callsign: 'BAW117', lat: 51.4, lng: -0.4 }];
for (const [name, body] of [['planes', { planes }]]) {
  const r = await fetch('https://api.adsb.lol/api/0/routeset', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'User-Agent': 'YashHoodaPortfolio/1.0' }, body: JSON.stringify(body) });
  const t = await r.text();
  console.log('ROUTESET', r.status, r.headers.get('content-type'), t.slice(0, 900));
}
const r2 = await fetch('https://api.adsb.lol/api/0/route/UAL1310', { headers: { 'User-Agent': 'YashHoodaPortfolio/1.0' } });
console.log('ROUTE1', r2.status, (await r2.text()).slice(0, 400));
