# frozen_string_literal: true

require "ipaddr"
require "yaml"

ROOT = File.expand_path(__dir__)
definition_path = ENV.fetch("TESTBED", "testbed.yaml")
definition_path = File.expand_path(definition_path, ROOT)
abort "testbed definition not found: #{definition_path}" unless File.file?(definition_path)

testbed = YAML.safe_load(File.read(definition_path), aliases: false)
abort "unsupported testbed schema" unless testbed["schemaVersion"] == 1

local_path = File.join(ROOT, "testbed.local.yaml")
local = File.file?(local_path) ? YAML.safe_load(File.read(local_path), aliases: false) : {}
provider_name = ENV["VAGRANT_DEFAULT_PROVIDER"] || local.dig("provider", "name")

machines = testbed.fetch("machines")
networks = testbed.fetch("networks")
expected_names = %w[core path-a path-b]
abort "machines must be exactly #{expected_names.join(', ')}" unless machines.keys.sort == expected_names.sort

machines.each do |machine_name, machine|
  machine.fetch("interfaces").each do |network_name, address|
    network = networks.fetch(network_name) { abort "unknown network #{network_name}" }
    cidr = IPAddr.new(network.fetch("cidr"))
    abort "#{machine_name} #{address} is outside #{network_name}" unless cidr.include?(IPAddr.new(address))
  end
end

Vagrant.configure("2") do |config|
  config.vm.box = testbed.dig("guest", "box") || "bento/ubuntu-24.04"
  config.vm.synced_folder ROOT, "/opt/5g-nwdaf-infrastructure/source",
    type: "rsync", rsync__auto: false,
    rsync__exclude: [".git/", ".vagrant/", "config/generated/", "config/local/"]

  machines.each do |machine_name, machine|
    config.vm.define machine_name do |node|
      node.vm.hostname = "5g-nwdaf-#{machine_name}"

      machine.fetch("interfaces").each do |_network_name, address|
        node.vm.network "private_network", ip: address
      end

      resources = machine.fetch("resources")
      node.vm.provider "virtualbox" do |vb|
        vb.name = "5g-nwdaf-#{machine_name}"
        vb.memory = resources.fetch("memoryMiB")
        vb.cpus = resources.fetch("cpus")
      end
      node.vm.provider "libvirt" do |lv|
        lv.memory = resources.fetch("memoryMiB")
        lv.cpus = resources.fetch("cpus")
      end

    end
  end

  config.vm.provider(provider_name) {} if provider_name
end
