# 被其他脚本 source：读凭据 + 两个小工具函数
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$HERE/env.sh" ]; then
  # shellcheck disable=SC1091
  . "$HERE/env.sh"
fi
: "${CX_HOST:?请先 cp env.example.sh env.sh 并填写，或直接 export CX_HOST/CX_ROBOT/CX_KEY}"
: "${CX_ROBOT:?缺少 CX_ROBOT}"
: "${CX_KEY:?缺少 CX_KEY}"

R="$CX_HOST/v1/robots/$CX_ROBOT"

# 打印 JSON：格式化并保留中文（json.tool 默认会把中文转成 \uXXXX，没法看）。
# 不是合法 JSON 时原样输出 —— 出错响应有时不是 JSON，别把它吞掉。
pretty() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    json.dump(json.loads(raw), sys.stdout, ensure_ascii=False, indent=2)
    print()
except ValueError:
    sys.stdout.write(raw)
'
  else
    cat
  fi
}

# 带标题的 GET：先打印状态码，再格式化响应体。
# 别用 -w 把状态码追加到响应体后面 —— 那样输出就不是合法 JSON 了，
# 格式化会退化成原样输出（中文还会被转义成 \uXXXX）。
show() {
  local body code
  body=$(mktemp)
  code=$(curl -sS -H "X-API-Key: $CX_KEY" -o "$body" -w '%{http_code}' "$2" || echo 000)
  printf '\n\033[1m%s\033[0m  [HTTP %s]\n' "$1" "$code"
  pretty < "$body"
  rm -f "$body"
}
