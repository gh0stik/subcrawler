pipeline {
    agent any

    triggers {
        pollSCM('H/5 * * * *')
    }
    
    stages {
        stage('build') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub', usernameVariable: 'USER', passwordVariable: 'PASS')]) {
                    sh "docker login -u ${USER} -p ${PASS}"
                    sh "docker build --no-cache -t gh0stik/subcrawler:1.0 ."
                }
            }
        }
        stage('push') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub', usernameVariable: 'USER', passwordVariable: 'PASS')]) {
                    sh "docker push gh0stik/subcrawler:1.0"
                }
            }
        }
    }
}
