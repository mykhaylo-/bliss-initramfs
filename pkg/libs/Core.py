# Copyright 2012-2015 Jonathan Vasquez <jvasquez1011@gmail.com>
# Licensed under the Simplified BSD License which can be found in the LICENSE file.

import os
import shutil
import re

from subprocess import call
from subprocess import check_output
from subprocess import CalledProcessError

import pkg.libs.Variables as var

from pkg.libs.Tools import Tools
from pkg.hooks.Base import Base
from pkg.hooks.Zfs import Zfs
from pkg.hooks.Luks import Luks
from pkg.hooks.Addon import Addon
from pkg.hooks.Firmware import Firmware
from pkg.hooks.Udev import Udev

# Contains the core of the application
class Core(object):
     # List of binaries (That will be 'ldd'ed later)
    _binset = set()

    # List of modules that will be compressed
    _modset = set()

    # Enable the 'base' hook since all initramfs will have this
    Base.Enable()

    @classmethod
    # Prints the menu and accepts user choice
    def PrintMenu(cls):
        # If the user didn't pass an option through the command line,
        # then ask them which initramfs they would like to generate.
        if not var.choice:
            print("Which initramfs would you like to generate:")
            Tools.PrintOptions()
            var.choice = Tools.Question("Current choice [1]: ")
            Tools.NewLine()

        # Enable the addons if the addon has files (modules) listed
        if Addon.GetFiles():
            Addon.Enable()

        # ZFS
        if var.choice == "1" or not var.choice:
            Zfs.Enable()
            Addon.Enable()
            Addon.AddFile("zfs")
        # Encrypted ZFS
        elif var.choice == "2":
            Luks.Enable()
            Zfs.Enable()
            Addon.Enable()
            Addon.AddFile("zfs")
        # Normal
        elif var.choice == "3":
            pass
        # Encrypted Normal
        elif var.choice == "4":
            Luks.Enable()
        # Exit
        elif var.choice == "5":
            Tools.Warn("Exiting.")
            quit(1)
        # Invalid Option
        else:
            Tools.Warn("Invalid Option. Exiting.")
            quit(1)

    # Creates the base directory structure
    @classmethod
    def CreateBaselayout(cls):
        for dir in var.baselayout:
            call(["mkdir", "-p", dir])

        # Create a symlink to this temporary directory at the home dir.
        # This will help us debug if anything (since the dirs are randomly
        # generated...)
        os.symlink(var.temp, var.tlink)

    # Ask the user if they want to use their current kernel, or another one
    @classmethod
    def GetDesiredKernel(cls):
        if not var.kernel:
            current_kernel = check_output(["uname", "-r"], universal_newlines=True).strip()

            message = "Do you want to use the current kernel: " + current_kernel + " [Y/n]: "
            var.choice = Tools.Question(message)
            Tools.NewLine()

            if var.choice == 'y' or var.choice == 'Y' or not var.choice:
                var.kernel = current_kernel
            elif var.choice == 'n' or var.choice == 'N':
                var.kernel = Tools.Question("Please enter the kernel name: ")
                Tools.NewLine()

                if not var.kernel:
                    Tools.Fail("You didn't enter a kernel. Exiting...")
            else:
                Tools.Fail("Invalid Option. Exiting.")

        # Set modules path to correct location and sets kernel name for initramfs
        var.modules = "/lib/modules/" + var.kernel + "/"
        var.lmodules = var.temp + "/" + var.modules
        var.initrd = "initrd-" + var.kernel

        # Check modules directory
        cls.VerifyModulesDirectory()

    # Check to make sure the kernel modules directory exists
    @classmethod
    def VerifyModulesDirectory(cls):
        if not os.path.exists(var.modules):
            Tools.Fail("The modules directory for " + var.modules + " doesn't exist!")

    # Make sure that the arch is x86_64
    @classmethod
    def VerifySupportedArchitecture(cls):
        if var.arch != "x86_64":
            Tools.Fail("Your architecture isn't supported. Exiting.")

    # Checks to see if the preliminary binaries exist
    @classmethod
    def VerifyPreliminaryBinaries(cls):
        Tools.Info("Checking preliminary binaries ...")

        # If the required binaries don't exist, then exit
        for binary in var.prel_bin:
            if not os.path.isfile(binary):
                Tools.BinaryDoesntExist(binary)

    # Compresses the kernel modules
    @classmethod
    def CompressKernelModules(cls):
        Tools.Info("Compressing kernel modules ...")

        cmd = "find " + var.lmodules + " -name " + "*.ko"
        results = check_output(cmd, shell=True, universal_newlines=True).strip()

        for module in results.split("\n"):
            cmd = "gzip -9 " + module
            callResult = call(cmd, shell=True)

            if callResult != 0:
                Tools.Fail("Unable to compress " + module + " !")

    # Generates the modprobe information
    @classmethod
    def GenerateModprobeInfo(cls):
        Tools.Info("Generating modprobe information ...")

        # Copy modules.order and modules.builtin just so depmod doesn't spit out warnings. -_-
        Tools.Copy(var.modules + "/modules.order")
        Tools.Copy(var.modules + "/modules.builtin")

        result = call(["depmod", "-b", var.temp, var.kernel])

        if result != 0:
            Tools.Fail("Depmod was unable to refresh the dependency information for your initramfs!")

    # Copies the firmware files if necessary
    @classmethod
    def CopyFirmware(cls):
        if Firmware.IsEnabled():
            Tools.Info("Copying firmware...")

            if os.path.isdir("/lib/firmware/"):
                if Firmware.IsCopyAllEnabled():
                    shutil.copytree("/lib/firmware/", var.temp + "/lib/firmware/")
                else:
                    # Copy the firmware in the files list
                    if Firmware.GetFiles():
                        try:
                            for fw in Firmware.GetFiles():
                                Tools.Copy(fw, directoryPrefix=var.firmwareDirectory)
                        except FileNotFoundError:
                            Tools.Warn("An error occured while copying the following firmware: " + fw)
                    else:
                        Tools.Warn("No firmware files were found in the firmware list!")
            else:
                Tools.Fail("The /lib/firmware/ directory does not exist")

    # Create the required symlinks
    @classmethod
    def CreateLinks(cls):
        Tools.Info("Creating symlinks ...")

        # Needs to be from this directory so that the links are relative
        os.chdir(var.lbin)

        # Create busybox links
        cmd = 'chroot ' + var.temp + ' /bin/busybox sh -c "cd /bin && /bin/busybox --install -s ."'

        callResult = call(cmd, shell=True)

        if callResult != 0:
            Tools.Fail("Unable to create busybox links via chroot!")

        # Create 'sh' symlink to 'bash'
        os.remove(var.temp + "/bin/sh")
        os.symlink("bash", "sh")

        # Switch to the kmod directory, delete the corresponding busybox
        # symlink and create the symlinks pointing to kmod
        if os.path.isfile(var.lsbin + "/kmod"):
            os.chdir(var.lsbin)
        elif os.path.isfile(var.lbin + "/kmod"):
            os.chdir(var.lbin)

        for link in Base.GetKmodLinks():
            os.remove(var.temp + "/bin/" + link)
            os.symlink("kmod", link)

    # Creates symlinks from library files found in each /usr/lib## dir to the /lib[32/64] directories
    @classmethod
    def CreateLibraryLinks(cls):
         # Set library symlinks
        if os.path.isdir(var.temp + "/usr/lib") and os.path.isdir(var.temp + "/lib64"):
            pcmd = 'find /usr/lib -iname "*.so.*" -exec ln -s "{}" /lib64 \;'
            cmd = 'chroot ' + var.temp + ' /bin/busybox sh -c "' + pcmd + '"'
            call(cmd, shell=True)

        if os.path.isdir(var.temp + "/usr/lib32") and os.path.isdir(var.temp + "/lib32"):
            pcmd = 'find /usr/lib32 -iname "*.so.*" -exec ln -s "{}" /lib32 \;'
            cmd = 'chroot ' + var.temp + ' /bin/busybox sh -c "' + pcmd + '"'
            call(cmd, shell=True)

        if os.path.isdir(var.temp + "/usr/lib64") and os.path.isdir(var.temp + "/lib64"):
            pcmd = 'find /usr/lib64 -iname "*.so.*" -exec ln -s "{}" /lib64 \;'
            cmd = 'chroot ' + var.temp + ' /bin/busybox sh -c "' + pcmd + '"'
            call(cmd, shell=True)

    # Copies files that udev uses, like /etc/udev/*, /lib/udev/*, etc
    @classmethod
    def CopyUdevSupportFiles(cls):
        if Udev.IsEnabled():
            # Activate udev support in 'init'
            call(["sed", "-i", "-e", var.useUdevLine + "s/0/1/", var.temp + "/init"])

            # Copy all of the udev files
            if os.path.isdir("/etc/udev/"):
                shutil.copytree("/etc/udev/", var.temp + "/etc/udev/")

            if os.path.isdir("/lib/udev/"):
                shutil.copytree("/lib/udev/", var.temp + "/lib/udev/")

            # Rename udevd and place in /sbin
            udev_path = Tools.GetUdevPath()
            systemd_dir = os.path.dirname(udev_path)

            if os.path.isfile(var.temp + udev_path) and udev_path != "/sbin/udevd":
                os.rename(var.temp + udev_path, var.temp + "/sbin/udevd")
                os.rmdir(var.temp + systemd_dir)

    # This functions does any last minute steps like copying zfs.conf,
    # giving init execute permissions, setting up symlinks, etc
    @classmethod
    def LastSteps(cls):
        Tools.Info("Performing finishing steps ...")

        # Create mtab file
        call(["touch", var.temp + "/etc/mtab"])

        if not os.path.isfile(var.temp + "/etc/mtab"):
            Tools.Fail("Error creating the mtab file. Exiting.")

        cls.CreateLibraryLinks()

        # Copy the init script
        shutil.copy(var.phome + "/files/init", var.temp)

        if not os.path.isfile(var.temp + "/init"):
            Tools.Fail("Error creating the init file. Exiting.")

        # Give execute permissions to the script
        cr = call(["chmod", "u+x", var.temp + "/init"])

        if cr != 0:
            Tools.Fail("Failed to give executive privileges to " + var.temp + "/init")

        # Fix 'poweroff, reboot' commands
        call("sed -i \"71a alias reboot='reboot -f' \" " + var.temp + "/etc/bash/bashrc", shell=True)
        call("sed -i \"71a alias poweroff='poweroff -f' \" " + var.temp + "/etc/bash/bashrc", shell=True)

        # Sets initramfs script version number
        call(["sed", "-i", "-e", var.initrdVersionLine + "s/0/" + var.version + "/", var.temp + "/init"])

        # Fix EDITOR/PAGER
        call(["sed", "-i", "-e", "12s:/bin/nano:/bin/vi:", var.temp + "/etc/profile"])
        call(["sed", "-i", "-e", "13s:/usr/bin/less:/bin/less:", var.temp + "/etc/profile"])

        # Copy all of the modprobe configurations
        if os.path.isdir("/etc/modprobe.d/"):
            shutil.copytree("/etc/modprobe.d/", var.temp + "/etc/modprobe.d/")

        cls.CopyUdevSupportFiles()

        # Any last substitutions or additions/modifications should be done here
        if Zfs.IsEnabled():
            # Enable ZFS in the init if ZFS is being used
            call(["sed", "-i", "-e", var.useZfsLine + "s/0/1/", var.temp + "/init"])

            # Copy zpool.cache into initramfs
            if os.path.isfile("/etc/zfs/zpool.cache"):
                Tools.Flag("Using your zpool.cache file ...")
                Tools.Copy("/etc/zfs/zpool.cache")
            else:
                Tools.Warn("No zpool.cache was found. It will not be used ...")

        # Enable LUKS in the init if LUKS is being used
        if Luks.IsEnabled():
            call(["sed", "-i", "-e", var.useLuksLine + "s/0/1/", var.temp + "/init"])

        # Enable ADDON in the init and add our modules to the initramfs
        # if addon is being used
        if Addon.IsEnabled():
            call(["sed", "-i", "-e", var.useAddonLine + "s/0/1/", var.temp + "/init"])
            call(["sed", "-i", "-e", var.addonModulesLine + "s/\"\"/\"" + " ".join(Addon.GetFiles()) + "\"/", var.temp + "/init"])

    # Create the initramfs
    @classmethod
    def CreateInitramfs(cls):
        Tools.Info("Creating the initramfs ...")

        # The find command must use the `find .` and not `find ${T}`
        # because if not, then the initramfs layout will be prefixed with
        # the ${T} path.
        os.chdir(var.temp)

        call(["find . -print0 | cpio -o --null --format=newc | gzip -9 > " +  var.home + "/" + var.initrd], shell=True)

        if not os.path.isfile(var.home + "/" + var.initrd):
            Tools.Fail("Error creating the initramfs. Exiting.")

    # Checks to see if the binaries exist, if not then emerge
    @classmethod
    def VerifyBinaries(cls):
        Tools.Info("Checking required files ...")

        # Check required base files
        cls.VerifyBinariesExist(Base.GetFiles())

        # Check required udev files
        if Udev.IsEnabled():
            Tools.Flag("Using udev")
            cls.VerifyBinariesExist(Udev.GetFiles())
        else:
            Tools.Warn("Not including udev. Booting using UUIDs will not be supported.")

        # Check required zfs files
        if Zfs.IsEnabled():
            Tools.Flag("Using ZFS")
            cls.VerifyBinariesExist(Zfs.GetFiles())

        # Check required luks files
        if Luks.IsEnabled():
            Tools.Flag("Using LUKS")
            cls.VerifyBinariesExist(Luks.GetFiles())

    # Checks to see that all the binaries in the array exist and errors if they don't
    @classmethod
    def VerifyBinariesExist(cls, vFiles):
        for file in vFiles:
            if not os.path.exists(file):
                Tools.BinaryDoesntExist(file)

    # Copies the required files into the initramfs
    @classmethod
    def CopyRequiredFiles(cls):
        Tools.Info("Copying required files ...")

        cls.FilterAndInstall(Base.GetFiles())

        if Udev.IsEnabled():
            cls.FilterAndInstall(Udev.GetFiles())

        if Zfs.IsEnabled():
            cls.FilterAndInstall(Zfs.GetFiles())

        if Luks.IsEnabled():
            cls.FilterAndInstall(Luks.GetFiles())

    # Filters and installs each file in the array into the initramfs
    @classmethod
    def FilterAndInstall(cls, vFiles):
        for file in vFiles:
            # If the application is a binary, add it to our binary set. If the application is not
            # a binary, then we will get a CalledProcessError because the output will be null.
            try:
                check_output('file -L ' + file.strip() + ' | grep "linked"', shell=True, universal_newlines=True).strip()
                cls._binset.add(file)
            except CalledProcessError:
                pass

            # Copy the file into the initramfs
            Tools.Copy(file)

    # Copy modules and their dependencies
    @classmethod
    def CopyModules(cls):
        moddeps = set()

        # Build the list of module dependencies
        if Addon.IsEnabled():
            Tools.Info("Copying modules ...")

            # Checks to see if all the modules in the list exist
            for file in Addon.GetFiles():
                try:
                    cmd = 'find ' + var.modules + ' -iname "' + file + '.ko" | grep ' + file + '.ko'
                    result = check_output(cmd, universal_newlines=True, shell=True).strip()
                    cls._modset.add(result)
                except CalledProcessError:
                    Tools.ModuleDoesntExist(file)

        # If a kernel has been set, try to update the module dependencies
        # database before searching it
        if var.kernel:
            try:
                result = call(["depmod", var.kernel])

                if result:
                    Tools.Fail("Error updating module dependency database!")
            except FileNotFoundError:
                    # This should never occur because the application checks
                    # that root is the user that is running the application.
                    # Non-administraative users normally don't have access
                    # to the 'depmod' command.
                    Tools.Fail("The 'depmod' command wasn't found.")

        # Get the dependencies for all the modules in our set
        for file in cls._modset:
            # Get only the name of the module
            match = re.search('(?<=/)[a-zA-Z0-9_-]+.ko', file)

            if match:
                sFile = match.group().split(".")[0]

                cmd = "modprobe -S " + var.kernel + " --show-depends " + sFile + " | awk -F ' ' '{print $2}'"
                results = check_output(cmd, shell=True, universal_newlines=True).strip()

                for i in results.split("\n"):
                    moddeps.add(i.strip())

        # Copy the modules/dependencies
        if moddeps:
            for module in moddeps:
                Tools.Copy(module)

            # Compress the modules and update module dependency database inside the initramfs
            cls.CompressKernelModules()
            cls.GenerateModprobeInfo()

    # Gets the library dependencies for all our binaries and copies them into our initramfs.
    @classmethod
    def CopyDependencies(cls):
        Tools.Info("Copying library dependencies ...")

        bindeps = set()

        # Get the interpreter name that is on this system
        result = check_output("ls " + var.lib64 + "/ld-linux-x86-64.so*", shell=True, universal_newlines=True).strip()

        # Add intepreter to deps since everything will depend on it
        bindeps.add(result)

        # Get the dependencies for the binaries we've collected and add them to
        # our bindeps set. These will all be copied into the initramfs later.
        for binary in cls._binset:
            cmd = "ldd " + binary + " | awk -F '=>' '{print $2}' | awk -F ' ' '{print $1}' | sed '/^ *$/d'"
            results = check_output(cmd, shell=True, universal_newlines=True).strip()

            if results:
                for library in results.split("\n"):
                    bindeps.add(library)

        # Copy all the dependencies of the binary files into the initramfs
        for library in bindeps:
            Tools.Copy(library)
