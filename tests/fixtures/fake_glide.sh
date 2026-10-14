#!/bin/sh
# A glide stand-in for the repair session tests. It records its arguments in
# $FAKE/glide-args and exits with the code in $FAKE/glide-rc. It lives in the
# repository, not in temporary storage, because temporary storage may be noexec.
echo "$@" >"$FAKE/glide-args"
exit "$(cat "$FAKE/glide-rc")"
