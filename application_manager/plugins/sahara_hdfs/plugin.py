# Copyright (c) 2017 UFCG-LSD.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import time
import threading
import subprocess
import uuid
import os

from application_manager import exceptions as ex
from application_manager.openstack import connector as os_connector
from application_manager.openstack import utils as os_utils
from application_manager.plugins import base
from application_manager.service import api
from application_manager.utils import monitor
from application_manager.utils import optimizer
from application_manager.utils import scaler
from application_manager.utils import spark
from application_manager.utils.logger import Log, configure_logging

from saharaclient.api.base import APIException as SaharaAPIException
from application_manager.utils.ids import ID_Generator
from application_manager.plugins.base import GenericApplicationExecutor

LOG = Log("SaharaHDFSPlugin", "sahara_hdfs_plugin.log")
application_time_log = Log("Application_time", "application_time.log")
instances_log = Log("Instances", "instances.log")
configure_logging()

class OpenStackSparkStandaloneApplicationExecutor(GenericApplicationExecutor):

    def __init__(self):
        self.application_state = "None"
        self.state_lock = threading.RLock()
        self.application_time = -1
        self.start_time = -1

    def get_application_state(self):
        with self.state_lock:
            state = self.application_state
        return state

    def update_application_state(self, state):
        print state
        with self.state_lock:
            self.application_state = state 

    def get_application_execution_time(self):
        return self.application_time

    def get_application_start_time(self):
        return self.start_time

    def start_application(self, data, spark_applications_ids, app_id):
        try:
            self.update_application_state("Running")

            # Broker Parameters
            user           = api.user
            password       = api.password
            project_id     = api.project_id
            auth_ip        = api.auth_ip
            domain         = api.domain
            public_key     = api.public_key
            key_path       = api.key_path
            log_path       = api.log_path
            log_container  = api.container
            hosts          = api.hosts

            # User Request Parameters
            net_id            = data['net_id']
            master_ng         = data['master_ng']
            slave_ng          = data['slave_ng']
            op_slave_ng       = data['slave_ng']
            plugin            = data['openstack_plugin']
            job_type          = data['job_type']
            version           = data['version']
            pred_cluster_size = data['cluster_size']
            req_cluster_size  = data['cluster_size']
            cluster_size      = data['cluster_size']
            args              = data['args']
            main_class        = data['main_class']
            job_bin_name      = data['job_binary_name']
            job_bin_url       = data['job_binary_url']
            image_id          = data['image_id']

            # Openstack Components
            connector = os_connector.OpenStackConnector(LOG)

            sahara = connector.get_sahara_client(user,
                                                 password, 
                                                 project_id,
                                                 auth_ip, 
                                                 domain)

            swift = connector.get_swift_client(user, 
                                               password, 
                                               project_id,
                                               auth_ip, 
                                               domain)

            # Trying to obtain an existing cluster by size
            LOG.log("%s | Cluster size: %s" % (time.strftime("%H:%M:%S"), 
                                               str(cluster_size)))

            cluster_id = connector.get_existing_cluster_by_size(sahara, 
                                                                cluster_size)

            # If cluster doesn't exists, create the cluster
            if not cluster_id:
                LOG.log("%s | Cluster does not exist. Creating cluster..." %
                        (time.strftime("%H:%M:%S")))

                cluster_id = self._create_cluster(sahara, connector,
                                                  req_cluster_size,
                                                  pred_cluster_size,
                                                  public_key, net_id, image_id,
                                                  plugin, version, master_ng,
                                                  slave_ng, op_slave_ng)

            LOG.log("%s | Cluster id: %s" % (time.strftime("%H:%M:%S"),
                                             cluster_id))

            # If exists, the execution datapath continues
            if cluster_id:
                master = connector.get_master_instance(sahara, cluster_id)['internal_ip']

                LOG.log("%s | Master is: %s" % (time.strftime("%H:%M:%S"),
                                                master))

                workers = connector.get_worker_instances(sahara, cluster_id)
                workers_id = []

                for worker in workers:
                    workers_id.append(worker['instance_id'])

                job_exec_id = str(uuid.uuid4())[0:7]
                LOG.log("%s | Job execution ID: %s" % (time.strftime("%H:%M:%S"), job_exec_id))

                # Defining params
                local_path = '/tmp/spark-jobs/' + job_exec_id
                hdfs_path = '/user/ubuntu/' + job_exec_id
                remote_path = 'ubuntu@' + master + ':' + local_path

                job_input_paths, job_output_path, job_params, job_container = self._get_job_params(args)
                job_binary_path = self._get_swift_path(job_bin_url)

                local_input_path  = local_path + '/input/'
                local_output_path = local_path + '/output/'
                local_binary_path = local_path + '/bin/'

                hdfs_input_path  = hdfs_path + '/input/'
                hdfs_output_path = hdfs_path + '/output/'
                hdfs_binary_path = hdfs_path + '/bin/'

                remote_output_path = remote_path + '/output/'
                
                # Create temporary job directories
                LOG.log("%s | Create temporary job directories" % (time.strftime("%H:%M:%S")))
                self._mkdir(local_input_path)
                self._mkdir(local_binary_path)

                # Pull data from swift
                LOG.log("%s | Pull data from swift" % (time.strftime("%H:%M:%S")))
                self._download_from_swift(connector, swift, job_input_paths, local_input_path, job_container)

                # Get job binary from swift
                LOG.log("%s | Get job binary from %s" % (time.strftime("%H:%M:%S"), job_binary_path))
                connector.download_file(swift, job_binary_path, local_binary_path, job_container)

                # Create cluster directories
                LOG.log("%s | Creating cluster directories" % (time.strftime("%H:%M:%S")))
                self._remote_command(key_path, master, 'mkdir -p %s' % local_path)

                # Copy input and binary from broker to cluster
                LOG.log("%s | Copying input and binary from broker to cluster" % (time.strftime("%H:%M:%S")))
                self._remote_copy(key_path, local_input_path, remote_path)
                self._remote_copy(key_path, local_binary_path, remote_path)

                # Submit job
                LOG.log("%s | Submit job" % (time.strftime("%H:%M:%S")))
                local_binary_file = local_binary_path + os.listdir(local_binary_path)[0]
                self._submit_job(key_path, master, main_class, local_binary_file, local_input_path, local_output_path, job_params)

                # Copy output from cluster to broker
                LOG.log("%s | Copying output from cluster to broker" % (time.strftime("%H:%M:%S")))
                self._remote_copy(key_path, remote_output_path, local_path)

                # Push data to swift
                LOG.log("%s | Push data to swift" % (time.strftime("%H:%M:%S")))
                connector.upload_directory(swift, local_output_path, job_output_path, job_container)

                LOG.log("Finished application execution")
                self.update_application_state("OK")

            else:
                #FIXME: exception type
                self.update_application_state("Error")
                raise ex.ClusterNotCreatedException()

            return "OK"

        except Exception as e:
            self.update_application_state("Error")
            LOG.log(str(e))

    def get_application_time(self):
        return self.application_time

    def _create_cluster(self, sahara, connector, req_cluster_size,
                        pred_cluster_size, public_key, net_id, image_id,
                        plugin, version, master_ng, slave_ng, op_slave_ng):
        LOG.log('Creating cluster')
        try:
            cluster_id = connector.create_cluster(sahara, req_cluster_size,
                                                  pred_cluster_size,
                                                  public_key, net_id,
                                                  image_id, plugin,
                                                  version, master_ng,
                                                  slave_ng, op_slave_ng)
        except SaharaAPIException:
            raise SaharaAPIException('Could not create clusters')

        return cluster_id

    def _get_job_params(self, args):
        in_paths = []
        others = []

        for arg in args:
            if arg.startswith('swift://'):
                if "input" in arg: in_paths.append(self._get_swift_path(arg))
                if "output" in arg: out_path = self._get_swift_path(arg)
            else:
                others.append(arg)

        container = self._get_swift_container(args[0])

        return in_paths, out_path, others, container

    def _get_swift_path(self, arg):
        splitted = arg.split('/')
        swift_path = splitted[3]

        for i in range(len(splitted[4:])):
            swift_path = swift_path + '/' + splitted[i+4]

        return swift_path

    def _get_swift_container(self, arg):
        splitted = arg.split('/')
        container = splitted[2]

        return container

    def _download_from_swift(self, connector, swift, swift_path, local_path, container):
        for path in swift_path:
            if os.path.isdir(path):
                connector.download_directory(swift, path, local_path, container)
            else:
                connector.download_file(swift, path, local_path, container) 

    def _push_to_hdfs(self, master, local_path, hdfs_path):
        hadoop_mkdir_command = "hadoop fs -mkdir -p %s" % (hdfs_path)
        ssh_command = "ssh -i /home/ubuntu/.ssh/bigsea ubuntu@%s '%s'" % (master, hadoop_mkdir_command)
        subprocess.call(ssh_command, shell=True)

        hadoop_command = "hadoop fs -copyFromLocal %s %s" % (local_path, hdfs_path)
        ssh_command = "ssh -i /home/ubuntu/.ssh/bigsea ubuntu@%s '%s'" % (master, hadoop_command)
        subprocess.call(ssh_command, shell=True)

    def _pull_from_hdfs(self, master, hdfs_path, local_path):
        hadoop_command = "hadoop fs -get %s %s" % (hdfs_path, local_path)
        subprocess.call("ssh -i /home/ubuntu/.ssh/bigsea ubuntu@%s '%s'" % (master, hadoop_command), shell=True)

    def _submit_job(self, key, master, main_class, job_binary_file, input_path, output_path, parameters):
        input_param = ''
        for input_file in os.listdir(input_path):
            input_param = input_param + 'file://' + input_path + input_file + ' '

        others = ''
        for param in parameters:
            others = others + param + ' '

        output_param = 'file://' + output_path

        spark_submit = '/opt/spark/bin/spark-submit --class ' + main_class + " " + job_binary_file + " " + input_param + " " + output_param + " " + others

        self._remote_command(key, master, spark_submit)



    def _mkdir(self, path):
        subprocess.call("mkdir -p %s" % path, shell=True)

    def _remote_command(self, key, master, command):
        subprocess.call("ssh -i %s ubuntu@%s %s" % (key, master, command), shell=True)

    def _remote_copy(self, key, source, destination):
        subprocess.call("scp -i %s -r %s %s" % (key, source, destination), shell=True)

class SaharaHDFSProvider(base.PluginInterface):

    def __init__(self):
        self.spark_applications_ids = []
        self.id_generator = ID_Generator()

    def get_title(self):
        return 'OpenStack Sahara HDFS'

    def get_description(self):
        return 'Plugin that allows utilization of created Spark Standalone clusters to run jobs'

    def to_dict(self):
        return {
            'name': self.name,
            'title': self.get_title(),
            'description': self.get_description(),
        }

    def execute(self, data):
        executor = OpenStackSparkStandaloneApplicationExecutor()
        app_id = "saharahdfs" + self.id_generator.get_ID()
        handling_thread = threading.Thread(target=executor.start_application,
                                           args=(data,
                                                 self.spark_applications_ids,
                                                 app_id))
        handling_thread.start()
        return (app_id, executor)
