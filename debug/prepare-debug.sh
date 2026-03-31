ip netns exec srbase-mgmt /opt/srlinux/python/virtual-env/bin/pip install debugpy

sr_cli -d -ec <<'EOF'
acl {
    acl-filter cpm type ipv4 {
        entry 666 {
            description "Allow remote python debugger"
            match {
                ipv4 {
                    protocol tcp
                }
                transport {
                    destination-port {
                        operator eq
                        value 55678
                    }
                }
            }
            action {
                accept {
                }
            }
        }
    }
}
EOF

# this won't be needed starting with 25.3
# but it doesn't hurt to keep it as well
# see https://github.com/microsoft/debugpy/issues/1835#issuecomment-2657827499
sed -i 's/exec(source, globals(), globals())/code_object = compile(source, self.path, "exec")\n        exec(code_object, globals(), globals())/' /opt/srlinux/python/virtual-env/lib/python3.11/dist-packages/srlinux/mgmt/cli/plugin_loader.py

DEBUG_FLAGS="-X frozen_modules=off -m debugpy --listen 0.0.0.0:55678"

if [ "$WAIT_FOR_DEBUG_CONN" == "true" ]; then
    DEBUG_FLAGS="$DEBUG_FLAGS --wait-for-client"
fi



sed -i "s/PYTHONIOENCODING=UTF-8 python/PYTHONIOENCODING=UTF-8 python $DEBUG_FLAGS/" /opt/srlinux/bin/sr_cli
