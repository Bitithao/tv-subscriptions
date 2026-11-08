// TAG Internet TV policy. No subscription URL or node credentials are stored here.
// Domain/IP data: https://github.com/Loyalsoldier/clash-rules
const main = (config) => {
  const nodes = (config.proxies || []).filter(p => /香港\s*\d+丨1x\s*HK/.test(p.name));
  if (!nodes.length) throw new Error('No ordinary Hong Kong nodes found; check TAG subscription naming before changing this filter.');
  config.proxies = nodes;
  delete config['proxy-providers'];
  config['proxy-groups'] = [{name:'HK-Normal',type:'url-test',proxies:nodes.map(p=>p.name),url:'https://www.gstatic.com/generate_204',interval:300,tolerance:150}];
  const providers = {};
  for (const name of ['private','lancidr','direct','gfw','telegramcidr','cncidr']) {
    providers[name] = {type:'http',behavior:name.endsWith('cidr')?'ipcidr':'domain',url:'https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/'+name+'.txt',path:'./ruleset/'+name+'.yaml',interval:86400,proxy:'HK-Normal'};
  }
  config['rule-providers'] = providers;
  config.rules = ['RULE-SET,private,DIRECT','RULE-SET,lancidr,DIRECT,no-resolve','RULE-SET,direct,DIRECT','RULE-SET,gfw,HK-Normal','RULE-SET,telegramcidr,HK-Normal,no-resolve','RULE-SET,cncidr,DIRECT,no-resolve','MATCH,DIRECT'];
  config.mode = 'rule';
  config['unified-delay'] = true;
  config['tcp-concurrent'] = true;
  return config;
};
