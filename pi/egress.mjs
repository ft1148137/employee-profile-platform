import { AsyncLocalStorage } from 'node:async_hooks';
import { lookup } from 'node:dns';
import { BlockList, isIP } from 'node:net';
import { Agent, DecoratorHandler, setGlobalDispatcher } from 'undici';

export const egress = new AsyncLocalStorage();
const forbidden = new BlockList();
forbidden.addSubnet('169.254.0.0', 16);
forbidden.addSubnet('fe80::', 10, 'ipv6');
forbidden.addAddress('fd00:ec2::254', 'ipv6');
const privateNet = new BlockList();
for (const [ip, bits] of [['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10],
  ['127.0.0.0', 8], ['172.16.0.0', 12], ['192.168.0.0', 16], ['192.0.0.0', 24],
  ['198.18.0.0', 15], ['224.0.0.0', 4], ['240.0.0.0', 4]]) privateNet.addSubnet(ip, bits);
for (const [ip, bits] of [['::', 128], ['::1', 128], ['fc00::', 7], ['ff00::', 8],
  ['2001:db8::', 32]]) privateNet.addSubnet(ip, bits, 'ipv6');
const loopback = new BlockList();
loopback.addSubnet('127.0.0.0', 8);
loopback.addAddress('::1', 'ipv6');

export function checkAddress(address, policy) {
  const family = isIP(address);
  const type = family === 6 ? 'ipv6' : 'ipv4';
  if (!family || forbidden.check(address, type) || (policy.catalog && privateNet.check(address, type)
    && !(process.env.PI_EGRESS_ALLOW_LOOPBACK === '1' && loopback.check(address, type))))
    throw new Error('Model network destination refused');
}

// A fresh connection per dispatch prevents pooled sockets from bypassing a later DNS check.
setGlobalDispatcher({ dispatch(options, handler) {
  const policy = egress.getStore();
  try {
    if (!policy) throw new Error('Missing model network policy');
    const destination = new URL(options.origin);
    if (!policy.catalog && destination.origin !== policy.origin) throw new Error('Model origin refused');
    const hostname = destination.hostname.replace(/^\[|\]$/g, '');
    if (isIP(hostname)) checkAddress(hostname, policy);
    const agent = new Agent({ connect: { lookup: (host, opts, callback) => {
      (policy.lookup ?? lookup)(host, { ...opts, all: true }, (error, addresses) => {
        try {
          if (error) throw error;
          if (!addresses?.length) throw new Error('No model addresses');
          for (const { address } of addresses) checkAddress(address, policy);
          callback(null, opts.all ? addresses : addresses[0].address, addresses[0].family);
        } catch (error) { callback(error); }
      });
    } } });
    return agent.dispatch(options, new class extends DecoratorHandler {
      onResponseStart(controller, status, ...args) {
        if (status >= 300 && status < 400) return controller.abort(new Error('Model redirect refused'));
        return super.onResponseStart(controller, status, ...args);
      }
      onResponseEnd(...args) { super.onResponseEnd(...args); void agent.close(); }
      onResponseError(...args) { super.onResponseError(...args); void agent.destroy(); }
    }(handler));
  } catch (error) { handler.onError(error); return false; }
} });
