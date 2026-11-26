"""Write version_info.txt for PyInstaller --version-file: product name/version on the exe (code signing rule)."""
import sys

v = sys.argv[1] if len(sys.argv) > 1 else '0.0.0'
n = [int(''.join(c for c in p if c.isdigit()) or 0) for p in v.split('.')][:4]
n += [0] * (4 - len(n))
t = tuple(n)
open('version_info.txt', 'w').write(f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'ZBM2 Industries'),
      StringStruct('FileDescription', 'ZBM2 CW Tool'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'ZBM2 CW Tool'),
      StringStruct('LegalCopyright', 'MIT License - ZBM2 Industries'),
      StringStruct('OriginalFilename', 'ZBM2 CW Tool.exe'),
      StringStruct('ProductName', 'ZBM2 CW Tool'),
      StringStruct('ProductVersion', '{v}')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
''')
print('version_info.txt', v)
