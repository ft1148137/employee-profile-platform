import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

export async function localCredentials(source, directory) {
  let temporary;
  try {
    const { tokens } = JSON.parse(await readFile(source, 'utf8'));
    const access = tokens.access_token;
    const claims = JSON.parse(Buffer.from(access.split('.')[1], 'base64url').toString('utf8'));
    const accountId = claims['https://api.openai.com/auth']?.chatgpt_account_id;
    if (typeof access !== 'string' || !access || typeof accountId !== 'string' || !accountId ||
        !Number.isFinite(claims.exp) || claims.exp * 1000 < Date.now() + 3600000)
      throw new Error('local_auth_unavailable');
    temporary = await mkdtemp(join(directory, 'codex-'));
    const path = join(temporary, 'access.json');
    await writeFile(path, JSON.stringify({ access, accountId, expires: claims.exp * 1000 }), { mode: 0o600, flag: 'wx' });
    return { access, remove: () => rm(temporary, { recursive: true, force: true }) };
  } catch {
    if (temporary) await rm(temporary, { recursive: true, force: true });
    throw new Error('local_auth_unavailable');
  }
}
