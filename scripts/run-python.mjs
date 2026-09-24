// Use an explicit interpreter when supplied, otherwise find the platform default.
import { spawnSync } from 'node:child_process'

const candidates = process.env.PYTHON ? [process.env.PYTHON]
  : process.platform === 'win32' ? ['python', 'python3', 'py'] : ['python3', 'python']
const executable = candidates.find(command => spawnSync(command, ['--version'], {
  encoding: 'utf8', windowsHide: true,
}).status === 0)
if (!executable) {
  console.error('Python 3.9+ is required. Install Python or set PYTHON to its executable path.')
  process.exit(1)
}
const child = spawnSync(executable, process.argv.slice(2), { stdio: 'inherit', windowsHide: true })
if (child.error) console.error('Could not start Python:', child.error.message)
process.exit(child.status ?? 1)
