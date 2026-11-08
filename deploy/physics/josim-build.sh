#!/bin/bash
# Pinned KLU-only build. No service configuration or credentials enter this stage.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1792989136
wqs_prefix=${1:-/out}
wqs_source=/build/source
mkdir -p "$wqs_source" "$wqs_prefix/bin" "$wqs_prefix/share/licenses"
fetch() {
    curl --fail --location --silent --show-error --retry 3 --max-filesize 67108864 "$1" -o "$2"
    printf '%s  %s\n' "$3" "$2" | sha256sum --check --status
}
fetch https://codeload.github.com/JoeyDelp/JoSIM/tar.gz/02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224 /build/josim.tar.gz 900d763011bcaba3413d18d159514aab74ec69d319346bc8ca646dc75fc6e4eb
fetch https://codeload.github.com/JoeyDelp/CMakeSuiteSparse/tar.gz/f4bc35614c99dd1853a807108d18bb56ea6d9348 /build/suite.tar.gz fbb4132ac9dc176845d6c36c160d9a13c7af4fb77b5b69c3fdd1086ea9d87c98
mkdir -p "$wqs_source/josim" /build/suite
# Only C/H files with their upstream notices are used from the wrapper archive.
tar -xzf /build/josim.tar.gz -C "$wqs_source/josim" --strip-components=1
tar -xzf /build/suite.tar.gz -C /build/suite --strip-components=1
for component in config amd btf colamd klu; do
    mkdir -p "$wqs_source/suitesparse/$component"
    cp -a "/build/suite/$component/src" "/build/suite/$component/include" "$wqs_source/suitesparse/$component/"
done
printf '%s  %s\n' eb727347b5933f7bb42c983c26ec63098e51d0d5a43fa7ceb865bfa28ce768ae /build/bounded-history.patch | sha256sum --check --status
(cd "$wqs_source/josim" && patch --batch --fuzz=0 -p1 < /build/bounded-history.patch)
cp /build/bounded-history.patch "$wqs_source/"

# Keep the upstream JoSIM build/warnings. Only dependency plumbing is changed.
python3 - <<'PY'
from pathlib import Path
p=Path('/build/source/josim/CMakeLists.txt')
s=p.read_text().replace('target_link_libraries(josim PRIVATE superlu)', '').replace('target_link_libraries(josim PRIVATE cblas)', '').replace('target_link_libraries(josim-cli PRIVATE superlu)', '').replace('target_link_libraries(josim-cli PRIVATE cblas)', '')
# The image installs the verified binary/source bundle directly, not an SDK export.
s=s[:s.index('# Install\n')]
p.write_text(s)
Path('/build/source/josim/cmake/commit_hash.cmake').write_text('add_definitions(-DGIT_COMMIT_HASH="02a34ee")\n')
Path('/build/source/josim/cmake/dependencies.cmake').write_text('add_subdirectory(${CMAKE_CURRENT_LIST_DIR}/../../suitesparse ${CMAKE_CURRENT_BINARY_DIR}/suitesparse)\n')
PY
cat > "$wqs_source/suitesparse/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.28)
project(WqsSuiteSparse LANGUAGES C)
foreach(component config amd btf colamd klu)
  file(GLOB sources CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/${component}/src/*.c")
  add_library(${component} STATIC ${sources})
  add_library(suitesparse::${component} ALIAS ${component})
  set_target_properties(${component} PROPERTIES POSITION_INDEPENDENT_CODE ON)
  target_compile_definitions(${component} PRIVATE DLONG)
  target_include_directories(${component} PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/${component}/include")
endforeach()
target_link_libraries(amd PUBLIC config)
target_link_libraries(btf PUBLIC config)
target_link_libraries(colamd PUBLIC config)
target_link_libraries(klu PUBLIC config amd btf colamd)
CMAKE
cat > "$wqs_source/rebuild.sh" <<'REBUILD'
#!/bin/bash
# Build a modified library/program from this complete source tree, without downloads.
set -euo pipefail
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH=1792989136
source_root=$(cd -- "$(dirname -- "$0")" && pwd)
cmake -S "$source_root/josim" -B "${1:?build directory}" \
  -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_STANDARD=99 \
  -DCMAKE_C_FLAGS="-ffile-prefix-map=$source_root=/src" \
  -DCMAKE_CXX_FLAGS="-ffile-prefix-map=$source_root=/src" -DUSING_OPENMP=OFF -DSLU=OFF
cmake --build "$1" --parallel 2
REBUILD
chmod 755 "$wqs_source/rebuild.sh"
"$wqs_source/rebuild.sh" /build/compiled
install -m755 /build/compiled/josim-cli "$wqs_prefix/bin/josim-cli"
# A source-matched diagnostic companion stays in the build stage, not the worker.
cmake -S "$wqs_source/josim" -B /build/audit -DBUILD_TESTING=OFF \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_STANDARD=99 -DUSING_OPENMP=OFF -DSLU=OFF \
  -DCMAKE_C_FLAGS="-ffile-prefix-map=$wqs_source=/src" \
  -DCMAKE_CXX_FLAGS="-ffile-prefix-map=$wqs_source=/src -DWQS_JOSIM_HISTORY_AUDIT"
cmake --build /build/audit --parallel 2
# No unused SuperLU/CBLAS objects or additional solver are distributed.
nm "$wqs_prefix/bin/josim-cli" > /build/symbols.txt
grep -q ' T klu_l_factor' /build/symbols.txt
if grep -Eq '( dCreate_CompCol_Matrix| dgssvx| cblas_)' /build/symbols.txt; then exit 1; fi
cp "$wqs_source/josim/LICENSE" "$wqs_prefix/share/licenses/JoSIM-LICENSE"
cp /build/WQS-LICENSE "$wqs_prefix/share/licenses/WQS-LICENSE"
cp /usr/share/common-licenses/LGPL-2.1 /usr/share/common-licenses/GPL-2 "$wqs_prefix/share/licenses/"
fetch https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/03350b0faef6b77d965ddb7c3cd3614a45376bfd/AMD/Doc/License.txt "$wqs_prefix/share/licenses/AMD.txt" e50fdd9a587f4860ce1d503b94576bfff47bccc421f758d1bdc8706c8cd36108
fetch https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/03350b0faef6b77d965ddb7c3cd3614a45376bfd/COLAMD/Doc/License.txt "$wqs_prefix/share/licenses/COLAMD.txt" 0e92b7bb3e216b8a4d1357b3d828043a4f758f762b2abc30b3440cf78063f80f
fetch https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/03350b0faef6b77d965ddb7c3cd3614a45376bfd/KLU/Doc/License.txt "$wqs_prefix/share/licenses/KLU.txt" 10cac083b3e0dea2a2e1eacbbd4c8ebc989ce6855903db633239840cd7a33482
fetch https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/03350b0faef6b77d965ddb7c3cd3614a45376bfd/BTF/Doc/License.txt "$wqs_prefix/share/licenses/BTF.txt" 56ba8d575568e117c26a525dc0b7ccd99950cf8d9a4fe27d21e90616d9e046e7
fetch https://raw.githubusercontent.com/DrTimothyAldenDavis/SuiteSparse/03350b0faef6b77d965ddb7c3cd3614a45376bfd/SuiteSparse_config/README.txt "$wqs_prefix/share/licenses/SuiteSparse_config.txt" dc371c731236da76ddc869e66f595827e85dae93a0d4f2f5ad68e7e4753545b2
# The config header supplies its copyright; AMD.txt supplies the common BSD terms.
cp -a "$wqs_prefix/share/licenses" "$wqs_source/licenses"
cp /build/josim-build.sh "$wqs_source/production-build.sh"
python3 - "$wqs_prefix" <<'PY'
import hashlib,json,pathlib,subprocess,sys
prefix=pathlib.Path(sys.argv[1]); source=pathlib.Path('/build/source')
manifest={'source_commit':'02a34ee5e7a3a6952b21ccc726fbf7a6d5e2b224',
 'suite_commit':'f4bc35614c99dd1853a807108d18bb56ea6d9348',
 'patch_sha256':hashlib.sha256((source/'bounded-history.patch').read_bytes()).hexdigest(),
 'binary_sha256':hashlib.sha256((prefix/'bin/josim-cli').read_bytes()).hexdigest(),
 'audit_binary_sha256':hashlib.sha256(pathlib.Path('/build/audit/josim-cli').read_bytes()).hexdigest(),
 'compiler':subprocess.check_output(['g++','--version'],text=True).splitlines()[0],
 'cmake':'3.28.3','source_date_epoch':1792989136,'solver':'KLU long, static; OpenMP off',
 'build_base':'python@sha256:dbbe4ceb97851e2e5fa83798b239811f871cb743b259ba3563737349f6bcfaa0',
 'source_files':{str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(source.rglob('*')) if p.is_file()},
 'dynamic_libraries':subprocess.check_output(['ldd',str(prefix/'bin/josim-cli')],text=True)}
# Runtime load addresses are nondeterministic, and are not part of provenance identity.
import re
manifest['runtime_libraries_sha256']={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
    for token in manifest['dynamic_libraries'].split()
    if token.startswith('/') and (path:=pathlib.Path(token)).is_file()}
manifest['dynamic_libraries']=re.sub(r' \(0x[0-9a-f]+\)', '',manifest['dynamic_libraries'])
(prefix/'share/build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
(source/'build-manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
PY
tar --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner -C "$wqs_source" -cf - . | gzip -n > "$wqs_prefix/share/corresponding-source.tar.gz"
