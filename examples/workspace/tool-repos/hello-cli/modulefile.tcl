#%Module1.0
##
## hello-cli/%VERSION% modulefile
##

proc ModulesHelp { } {
    puts stderr "hello-cli version %VERSION%"
    puts stderr "A friendly greeting tool"
}

module-whatis "hello-cli version %VERSION%"

conflict hello-cli

set root %ROOT%

prepend-path PATH $root/bin
