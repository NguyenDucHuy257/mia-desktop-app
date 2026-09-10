const crypto = require('node:crypto');
const { execFile } = require('node:child_process');
const { buildMiaV1Candidates, buildSerialPhoneHashes } = require('./legacy-formulas.cjs');

const HARDWARE_FIELDS = Object.freeze([
  'system_uuid', 'bios_serial', 'baseboard_serial',
  'machine_guid', 'cpu_id', 'disk_serial',
]);
const INVALID_VALUES = new Set([
  '', 'N/A', 'NA', 'NONE', 'NULL', 'UNKNOWN', 'DEFAULT STRING',
  'TO BE FILLED BY O.E.M.', '00000000-0000-0000-0000-000000000000',
  'FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF', 'SYSTEM SERIAL NUMBER',
]);
const HARDWARE_SCRIPT = String.raw`
$ErrorActionPreference = 'SilentlyContinue'
$disks = @(Get-CimInstance Win32_DiskDrive | ForEach-Object {
  [ordered]@{ SerialNumber = [string]$_.SerialNumber; Size = [string]$_.Size }
})
$physical = @($disks | Where-Object { [decimal]$_.Size -gt 0 } | Select-Object -First 1)
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$machineGuid = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid).MachineGuid
[ordered]@{
  system_uuid = [string](Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1 -ExpandProperty UUID)
  bios_serial = [string](Get-CimInstance Win32_BIOS | Select-Object -First 1 -ExpandProperty SerialNumber)
  baseboard_serial = [string](Get-CimInstance Win32_BaseBoard | Select-Object -First 1 -ExpandProperty SerialNumber)
  machine_guid = [string]$machineGuid
  cpu_id = [string]$cpu.ProcessorId
  disk_serial = if ($physical.Count) { [string]$physical[0].SerialNumber } else { '' }
  disks = $disks
} | ConvertTo-Json -Compress -Depth 4
`.trim();

function normalizeHardwareValue(value) {
  return String(value ?? '').trim().replace(/\s+/g, ' ').toUpperCase();
}

function hashHardwareSignal(name, value) {
  return crypto.createHash('sha256').update(`${name}:${value}`, 'utf8').digest('hex');
}

function buildDeviceEvidence(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new TypeError('invalid hardware profile response');
  }
  const hardware = {};
  for (const name of HARDWARE_FIELDS) {
    const normalized = normalizeHardwareValue(payload[name]);
    if (!normalized || INVALID_VALUES.has(normalized)) continue;
    hardware[name] = hashHardwareSignal(name, normalized);
  }
  if (Object.keys(hardware).length < 3) {
    const error = new Error('insufficient hardware signals');
    error.code = 'insufficient_hardware';
    throw error;
  }
  const disks = Array.isArray(payload.disks) ? payload.disks : payload.disks ? [payload.disks] : [];
  const candidates = buildMiaV1Candidates(disks);
  return Object.freeze({
    hardware: Object.freeze(hardware),
    valid_fields: Object.freeze(Object.keys(hardware).sort()),
    legacy: Object.freeze({
      exact_key_candidates: Object.freeze(candidates.map((item) => item.exact_key)),
      hash29_candidates: Object.freeze(candidates.map((item) => item.hash29)),
      serial_phone_hash29_candidates: Object.freeze(buildSerialPhoneHashes(disks)),
      detected_schema: Object.freeze(candidates.length ? ['mia_v1_disk_hash29'] : []),
    }),
  });
}

function runHardwareQuery(execFileImpl = execFile) {
  return new Promise((resolve, reject) => {
    execFileImpl('powershell.exe', [
      '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
      '-Command', HARDWARE_SCRIPT,
    ], { windowsHide: true, timeout: 15_000, maxBuffer: 512 * 1024, encoding: 'utf8' }, (error, stdout) => {
      if (error) {
        const wrapped = new Error('hardware profile query failed');
        wrapped.code = error.killed ? 'hardware_query_timeout' : 'hardware_query_failed';
        reject(wrapped);
        return;
      }
      try {
        resolve(JSON.parse(String(stdout || '').trim() || '{}'));
      } catch {
        const wrapped = new Error('hardware profile response is invalid');
        wrapped.code = 'hardware_query_invalid';
        reject(wrapped);
      }
    });
  });
}

async function collectDeviceEvidence(options = {}) {
  if (process.platform !== 'win32' && !options.allowNonWindows) {
    const error = new Error('hardware profile collection requires Windows');
    error.code = 'hardware_platform_unsupported';
    throw error;
  }
  const payload = options.payload || await runHardwareQuery(options.execFileImpl);
  return buildDeviceEvidence(payload);
}

module.exports = {
  HARDWARE_FIELDS,
  buildDeviceEvidence,
  collectDeviceEvidence,
  hashHardwareSignal,
  normalizeHardwareValue,
  runHardwareQuery,
};
