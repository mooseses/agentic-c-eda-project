#!/bin/bash
# benchmarks/generate_traffic.sh
# Generates realistic syslog traffic for System 1 benchmarking
# Usage: ./generate_traffic.sh [duration_seconds]

DURATION=${1:-60}
END_TIME=$((SECONDS + DURATION))

# Attacker IPs
ATTACKERS=("185.143.223.47" "92.118.160.10" "45.33.32.156" "103.41.167.89" "176.111.174.200")
# Common bruteforce usernames
USERS=("root" "admin" "ubuntu" "test" "user" "guest" "oracle" "postgres" "mysql" "deploy" "git" "jenkins")
# Ports that might be scanned
SCAN_PORTS=(21 22 23 25 80 110 139 443 445 3306 3389 5432 8080 8443)
# Legitimate users
LEGIT_USERS=("pete" "developer" "admin")
# Internal IPs
INTERNAL_IPS=("10.0.0.50" "10.0.0.100" "10.0.0.150" "10.0.0.200")

echo "============================================================"
echo "  Traffic Generator for System 1 Benchmark"
echo "============================================================"
echo "  Duration: ${DURATION}s"
echo "  Press Ctrl+C to stop early"
echo "============================================================"
echo ""

count=0

while [ $SECONDS -lt $END_TIME ]; do
    # Weighted random event type
    RAND=$((RANDOM % 100))
    
    if [ $RAND -lt 30 ]; then
        # 30% - SSH brute force attempts
        ATTACKER=${ATTACKERS[$((RANDOM % ${#ATTACKERS[@]}))]}
        USER=${USERS[$((RANDOM % ${#USERS[@]}))]}
        PORT=$((50000 + RANDOM % 10000))
        logger -t sshd -p auth.warning "Failed password for ${USER} from ${ATTACKER} port ${PORT} ssh2"
        ((count++))
        
    elif [ $RAND -lt 45 ]; then
        # 15% - Invalid user SSH attempts
        ATTACKER=${ATTACKERS[$((RANDOM % ${#ATTACKERS[@]}))]}
        USER=${USERS[$((RANDOM % ${#USERS[@]}))]}
        PORT=$((50000 + RANDOM % 10000))
        logger -t sshd -p auth.warning "Invalid user ${USER} from ${ATTACKER} port ${PORT}"
        ((count++))
        
    elif [ $RAND -lt 55 ]; then
        # 10% - Port scan (iptables log style)
        ATTACKER=${ATTACKERS[$((RANDOM % ${#ATTACKERS[@]}))]}
        PORT=${SCAN_PORTS[$((RANDOM % ${#SCAN_PORTS[@]}))]}
        logger -t kernel -p kern.info "[Sentinel] IN=eth0 OUT= SRC=${ATTACKER} DST=10.0.0.5 PROTO=TCP SPT=$((40000 + RANDOM % 10000)) DPT=${PORT} SYN"
        ((count++))
        
    elif [ $RAND -lt 65 ]; then
        # 10% - Successful legitimate login
        USER=${LEGIT_USERS[$((RANDOM % ${#LEGIT_USERS[@]}))]}
        INTERNAL=${INTERNAL_IPS[$((RANDOM % ${#INTERNAL_IPS[@]}))]}
        logger -t sshd -p auth.info "Accepted publickey for ${USER} from ${INTERNAL} port $((50000 + RANDOM % 10000)) ssh2"
        ((count++))
        
    elif [ $RAND -lt 75 ]; then
        # 10% - Sudo commands
        USER=${LEGIT_USERS[$((RANDOM % ${#LEGIT_USERS[@]}))]}
        CMDS=("apt update" "systemctl restart nginx" "docker ps" "tail -f /var/log/syslog" "netstat -tlnp")
        CMD=${CMDS[$((RANDOM % ${#CMDS[@]}))]}
        logger -t sudo -p auth.info "${USER} : TTY=pts/0 ; PWD=/home/${USER} ; USER=root ; COMMAND=/usr/bin/${CMD}"
        ((count++))
        
    elif [ $RAND -lt 85 ]; then
        # 10% - Internal trusted traffic (should be filtered)
        INTERNAL=${INTERNAL_IPS[$((RANDOM % ${#INTERNAL_IPS[@]}))]}
        TRUSTED_PORT=$((RANDOM % 2 == 0 ? 22 : 80))
        logger -t kernel -p kern.info "[Sentinel] IN=eth0 OUT= SRC=${INTERNAL} DST=10.0.0.5 PROTO=TCP DPT=${TRUSTED_PORT}"
        ((count++))
        
    elif [ $RAND -lt 92 ]; then
        # 7% - System noise (CRON, systemd - should be filtered)
        NOISE_MSGS=("CRON[1234]: (root) CMD (/usr/lib/php/sessionclean)" 
                    "systemd-logind[456]: New session 42 of user pete."
                    "systemd[1]: Started Session 42 of user pete.")
        MSG=${NOISE_MSGS[$((RANDOM % ${#NOISE_MSGS[@]}))]}
        logger -t "${MSG%% *}" -p daemon.info "${MSG#* }"
        ((count++))
        
    else
        # 8% - mDNS/broadcast noise (should be filtered)
        logger -t kernel -p kern.info "[Sentinel] IN=eth0 OUT= SRC=10.0.0.1 DST=224.0.0.251 PROTO=UDP DPT=5353"
        ((count++))
    fi
    
    # Random delay 50-200ms
    sleep 0.$((50 + RANDOM % 150))
    
    # Progress every 50 events
    if [ $((count % 50)) -eq 0 ]; then
        echo "[+] Generated ${count} events..."
    fi
done

echo ""
echo "============================================================"
echo "  Done! Generated ${count} events in ${DURATION}s"
echo "============================================================"
echo ""
echo "Event breakdown (approximate):"
echo "  - SSH brute force:     ~30%"
echo "  - Invalid SSH users:   ~15%"
echo "  - Port scans:          ~10%"
echo "  - Legitimate logins:   ~10%"
echo "  - Sudo commands:       ~10%"
echo "  - Internal traffic:    ~10% (should be filtered)"
echo "  - System noise:        ~15% (should be filtered)"
echo ""
echo "Expected reduction: ~25-35% filtered by System 1"
