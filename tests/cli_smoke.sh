#!/bin/bash
# CLI smoke tests for familylink
set +e
export FAMILYLINK_HOME=/tmp/fl_test_$$
rm -rf $FAMILYLINK_HOME
PASS=0; FAIL=0
check() { if [ "$1" = "$2" ]; then echo "PASS: $3"; PASS=$((PASS+1)); else echo "FAIL: $3 (expected=$1 got=$2)"; FAIL=$((FAIL+1)); fi }

echo "=== 1. init ==="
familylink init > /tmp/out1.txt 2>&1
check 0 $? "init exit 0"
ls -la $FAMILYLINK_HOME
DB_PERM=$(stat -c '%a' $FAMILYLINK_HOME/familylink.db 2>/dev/null)
KEY_PERM=$(stat -c '%a' $FAMILYLINK_HOME/master.key 2>/dev/null)
check 600 "$DB_PERM" "DB perms 600"
check 600 "$KEY_PERM" "Key perms 600"

echo "=== 2. family-head login ==="
familylink family-head login -i test@example.com -l Home > /tmp/out2.txt 2>&1
RC=$?
cat /tmp/out2.txt
check 0 $RC "family-head login exit 0"
grep -q "test@example.com" /tmp/out2.txt && echo "LEAK: email not masked" && FAIL=$((FAIL+1)) || echo "PASS: email masked"

echo "=== 3. job create ==="
familylink job create -c Child -b 2016-08-17 -g Guardian -o create --yes > /tmp/out3.txt 2>&1
RC=$?
cat /tmp/out3.txt
check 0 $RC "job create exit 0"
grep -q "families.google.com" /tmp/out3.txt && echo "PASS: handoff URL shown" && PASS=$((PASS+1)) || { echo "FAIL: no handoff URL"; FAIL=$((FAIL+1)); }
JOB_ID=$(grep -oE 'job_[0-9a-f]+' /tmp/out3.txt | head -1)
echo "JOB_ID=$JOB_ID"

echo "=== 4. idempotency ==="
familylink job create -c Child -b 2016-08-17 -g Guardian -o create --yes > /tmp/out4.txt 2>&1
RC=$?
cat /tmp/out4.txt
check 1 $RC "duplicate job create exit 1"
grep -qi "identical job already exists" /tmp/out4.txt && echo "PASS: dup message" && PASS=$((PASS+1)) || { echo "FAIL: no dup message"; FAIL=$((FAIL+1)); }

echo "=== 5. invalid birth dates ==="
familylink job create -c C2 -b 2020-02-30 -g G -o create --yes > /tmp/out5a.txt 2>&1
check 1 $? "invalid date 2020-02-30 exit 1"
cat /tmp/out5a.txt
familylink job create -c C3 -b 2999-01-01 -g G -o create --yes > /tmp/out5b.txt 2>&1
check 1 $? "future date 2999-01-01 exit 1"
cat /tmp/out5b.txt

echo "=== 6. confirm ==="
familylink confirm $JOB_ID --result created > /tmp/out6.txt 2>&1
RC=$?
cat /tmp/out6.txt
check 0 $RC "confirm exit 0"
familylink confirm $JOB_ID --result created > /tmp/out6b.txt 2>&1
check 1 $? "re-confirm terminal fail exit 1"
cat /tmp/out6b.txt

echo "=== 7. simulate-failure (need new job) ==="
familylink job create -c ChildB -b 2015-05-05 -g Guardian -o create --yes > /tmp/out7.txt 2>&1
JOB2=$(grep -oE 'job_[0-9a-f]+' /tmp/out7.txt | head -1)
echo "JOB2=$JOB2"
familylink simulate-failure $JOB2 -k transient > /tmp/out7a.txt 2>&1
check 0 $? "simulate-failure transient exit 0"
cat /tmp/out7a.txt
familylink simulate-failure $JOB2 -k permanent > /tmp/out7b.txt 2>&1
check 0 $? "simulate-failure permanent exit 0"
cat /tmp/out7b.txt

echo "=== 8. resume permanently failed refuses ==="
echo y | familylink resume $JOB2 > /tmp/out8.txt 2>&1
RC=$?
cat /tmp/out8.txt
check 1 $RC "resume on permanent fail exit 1"

echo "=== 9. cancel + jobs list ==="
familylink job create -c ChildC -b 2015-05-05 -g Guardian -o create --yes > /tmp/out9c.txt 2>&1
JOB3=$(grep -oE 'job_[0-9a-f]+' /tmp/out9c.txt | head -1)
familylink cancel $JOB3 > /tmp/out9.txt 2>&1
check 0 $? "cancel exit 0"
cat /tmp/out9.txt
familylink jobs > /tmp/out9b.txt 2>&1
check 0 $? "jobs list exit 0"
cat /tmp/out9b.txt

echo "=== 10. audit redaction ==="
familylink audit $JOB_ID > /tmp/out10.txt 2>&1
check 0 $? "audit exit 0"
cat /tmp/out10.txt
grep -q "Child" /tmp/out10.txt && { echo "FAIL: full name Child leaked in audit"; FAIL=$((FAIL+1)); } || { echo "PASS: no Child leaked"; PASS=$((PASS+1)); }
grep -q "Guardian" /tmp/out10.txt && { echo "FAIL: full name Guardian leaked"; FAIL=$((FAIL+1)); } || { echo "PASS: no Guardian leaked"; PASS=$((PASS+1)); }

echo "=== 11. pending job limit ==="
export FAMILYLINK_HOME=/tmp/fl_test_limit_$$
rm -rf $FAMILYLINK_HOME
export FAMILYLINK_MAX_PENDING_JOBS=2
familylink init > /dev/null 2>&1
familylink family-head login -i a@b.com -l Home > /dev/null 2>&1
familylink job create -c A -b 2015-01-01 -g G -o create --yes > /tmp/lim1.txt 2>&1
check 0 $? "1st pending ok"
familylink job create -c B -b 2015-01-02 -g G -o create --yes > /tmp/lim2.txt 2>&1
check 0 $? "2nd pending ok"
familylink job create -c C -b 2015-01-03 -g G -o create --yes > /tmp/lim3.txt 2>&1
RC=$?
cat /tmp/lim3.txt
check 1 $RC "3rd pending fails"
grep -qi "pending" /tmp/lim3.txt && echo "PASS: pending-limit msg" && PASS=$((PASS+1)) || { echo "FAIL: no pending-limit msg"; FAIL=$((FAIL+1)); }

echo "==================================="
echo "PASS=$PASS FAIL=$FAIL"
exit $FAIL
